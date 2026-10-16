"""HTTP API for the Countersign console. Credentials stay server-side; the browser gets case data only."""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import lru_cache

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db, demo
from .agent import AgentError
from .config import get_settings
from .paypal import PayPalError, PayPalSandbox
from .service import CaseError, CaseNotFound, Countersign


class JsonFormatter(logging.Formatter):
    RESERVED = set(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in self.RESERVED}
        return json.dumps({"ts": self.formatTime(record), "level": record.levelname, "logger": record.name,
                           "msg": record.getMessage(), **extra}, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@lru_cache(maxsize=1)
def service() -> Countersign:
    settings = get_settings()
    return Countersign(settings, PayPalSandbox(settings))


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    db.migrate(get_settings().db_url)
    yield


app = FastAPI(title="Countersign", version="0.1.0", lifespan=lifespan)
log = logging.getLogger("countersign.api")


@app.middleware("http")
async def request_log(request: Request, call_next):
    request_id, started = str(uuid.uuid4()), time.perf_counter()
    response = await call_next(request)
    log.info("request", extra={"request_id": request_id, "method": request.method, "path": request.url.path,
                               "status": response.status_code, "ms": round((time.perf_counter() - started) * 1000)})
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(CaseError)
async def case_error(_: Request, exc: CaseError):
    if isinstance(exc, CaseNotFound):
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": str(exc)}})
    return JSONResponse(status_code=409, content={"error": {"code": "INVALID_STATE", "message": str(exc)}})


@app.exception_handler(PayPalError)
async def paypal_error(_: Request, exc: PayPalError):
    log.warning("paypal error", extra={"status": exc.status, "issue": exc.issue, "debug_id": exc.debug_id})
    return JSONResponse(status_code=502, content={"error": {"code": "PAYPAL_ERROR", "message": exc.issue,
                                                            "debug_id": exc.debug_id}})


@app.exception_handler(demo.DemoLimit)
async def demo_limit(_: Request, exc: demo.DemoLimit):
    return JSONResponse(status_code=exc.status, content={"error": {"code": "DEMO_LIMIT", "message": str(exc)}})


@app.exception_handler(AgentError)
async def agent_error(_: Request, exc: AgentError):
    return JSONResponse(status_code=503, content={"error": {"code": "AGENT_UNAVAILABLE", "message": str(exc)}})


def require_operator(authorization: str | None = Header(default=None)) -> None:
    """Every case endpoint moves money or exposes case data: require the server-side operator token."""
    expected = f"Bearer {get_settings().operator_token}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="operator authentication required")


OPERATOR = [Depends(require_operator)]


def demo_session(x_countersign_session: str | None = Header(default=None)) -> str | None:
    """Judge mode: the web proxy (the only holder of the operator token) names the visitor's demo session."""
    if not get_settings().demo_mode:
        return None
    if not x_countersign_session or not demo.SESSION_RE.fullmatch(x_countersign_session):
        raise HTTPException(status_code=401, detail="demo session required")
    return x_countersign_session


def owned(case_id: uuid.UUID, session: str | None) -> str:
    """In judge mode a case is visible only to the session that created it; others get 404, not 403."""
    cid = str(case_id)
    if session is not None and service()._case(cid).get("session_id") != session:
        raise CaseNotFound("case not found")
    return cid


class CreateCase(BaseModel):
    operator_instruction: str = Field(default="", max_length=2000)
    untrusted_message: str = Field(default="", max_length=4000)
    seed: str | None = Field(default=None, pattern=r"^F\d{1,2}$")  # judge mode: frozen case id
    payment_amount: str = Field(default="1.00", pattern=r"^\d{1,4}(\.\d{1,2})?$")
    guard: str = Field(default="countersign", pattern=r"^(countersign|cap|none)$")  # cap/none: evaluation baselines


class ConfirmMandate(BaseModel):
    action: str = Field(pattern=r"^refund$")
    capture_id: str = Field(pattern=r"^[A-Z0-9]{10,30}$")
    amount: str = Field(pattern=r"^\d{1,9}(\.\d{1,2})?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class RunAgent(BaseModel):
    model: str = Field(default="gemini:gemini-2.5-flash",
                       pattern=r"^(gemini:[\w.\-]+|ollama:[\w.:\-]+|scripted(-dup)?:\d{1,4}(\.\d{1,2})?)$")


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@app.get("/api/health")
def health():
    return {"status": "ok", "paypal": "sandbox", "demo_mode": get_settings().demo_mode}


@app.get("/api/seeds", dependencies=OPERATOR)
def seeds():
    return {"seeds": demo.seeds(), "demo_mode": get_settings().demo_mode}


@app.get("/api/evidence", dependencies=OPERATOR)
def evidence():
    return {"replay": demo.replay()}


Session = Depends(demo_session)


@app.post("/api/cases", status_code=201, dependencies=OPERATOR)
def create_case(body: CreateCase, session: str | None = Session):
    if session is None:  # operator console: free text allowed, as before
        if len(body.operator_instruction) < 5 or not body.untrusted_message:
            raise HTTPException(status_code=422, detail="operator_instruction and untrusted_message are required")
        case_id = service().create_case(body.operator_instruction, body.untrusted_message, body.payment_amount,
                                        body.guard, seed=body.seed)
        return _jsonable(service().view(case_id))
    seed = demo.seed(body.seed or "")
    demo.check_case_caps(service().demo_usage(session))
    case_id = service().create_case(seed["operator_instruction"], seed["untrusted_message"], "1.00", "countersign",
                                    session_id=session, seed=seed["id"])
    return _jsonable(service().view(case_id))


@app.get("/api/cases/{case_id}", dependencies=OPERATOR)
def get_case(case_id: uuid.UUID, session: str | None = Session):
    return _jsonable(service().view(owned(case_id, session)))


@app.post("/api/cases/{case_id}/mandate", dependencies=OPERATOR)
def confirm_mandate(case_id: uuid.UUID, body: ConfirmMandate, session: str | None = Session):
    cid = owned(case_id, session)
    service().confirm_mandate(cid, body.action, body.capture_id, body.amount, body.currency)
    return _jsonable(service().view(cid))


@app.post("/api/cases/{case_id}/agent", dependencies=OPERATOR)
def run_agent(case_id: uuid.UUID, body: RunAgent, session: str | None = Session):
    cid, model = owned(case_id, session), body.model
    if session is not None:
        model = demo.agent_for(service()._case(cid)["seed"], model)
        demo.check_llm_cap(model, service().demo_usage(session))
    service().run_agent(cid, model)
    return _jsonable(service().view(cid))


@app.post("/api/cases/{case_id}/tool-calls/{tool_call_id}/approve-correction", dependencies=OPERATOR)
def approve_correction(case_id: uuid.UUID, tool_call_id: uuid.UUID, session: str | None = Session):
    cid = owned(case_id, session)
    service().approve_correction(cid, str(tool_call_id))
    return _jsonable(service().view(cid))


@app.post("/api/cases/{case_id}/tool-calls/{tool_call_id}/reject", dependencies=OPERATOR)
def reject(case_id: uuid.UUID, tool_call_id: uuid.UUID, session: str | None = Session):
    cid = owned(case_id, session)
    service().reject(cid, str(tool_call_id))
    return _jsonable(service().view(cid))


@app.post("/api/cases/{case_id}/verify", dependencies=OPERATOR)
def verify(case_id: uuid.UUID, session: str | None = Session):
    cid = owned(case_id, session)
    service().verify(cid)
    return _jsonable(service().view(cid))


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP", "message": exc.detail}})
