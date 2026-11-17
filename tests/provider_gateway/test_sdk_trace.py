from rai_gateway.provider_gateway import sdk_trace
from rai_gateway.provider_gateway.auth import extract_runtime_secret


def test_detects_vertex_from_path() -> None:
    info = sdk_trace.detect(
        "/providers/genai/v1beta1/projects/p1/locations/global/publishers/google/"
        "models/gemini-3.1-flash-lite:streamGenerateContent"
    )
    assert (info.api, info.project, info.location) == ("vertex", "p1", "global")
    assert info.model == "gemini-3.1-flash-lite"
    assert info.streaming


def test_detects_gemini_developer_api() -> None:
    info = sdk_trace.detect("/providers/genai/v1beta/models/gemini-2.5-flash:generateContent")
    assert info.api == "gemini"
    assert info.project is None
    assert not info.streaming


def test_unified_prefix_routes_to_matching_adapter() -> None:
    vertex = sdk_trace.resolve_unified_path(
        "/providers/genai/v1beta1/projects/p/locations/global/publishers/google/"
        "models/m:generateContent"
    )
    gemini = sdk_trace.resolve_unified_path(
        "/providers/genai/v1beta/models/m:generateContent"
    )
    assert vertex.startswith("/providers/vertex-ai/v1beta1/projects/")
    assert gemini.startswith("/providers/google-ai/v1beta/models/")
    assert sdk_trace.resolve_unified_path("/providers/openai/v1/x") == "/providers/openai/v1/x"


def test_runtime_secret_accepts_goog_api_key_last() -> None:
    assert extract_runtime_secret({"x-goog-api-key": "rai_rt_a"}) == "rai_rt_a"
    assert (
        extract_runtime_secret({"x-rai-agent-key": "k1", "x-goog-api-key": "k2"}) == "k1"
    )
