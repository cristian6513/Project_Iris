"""Provider discovery and an explicit response check in the private Hermes Python.

JSON arrives on stdin and stdout carries only sanitized results. Hermes owns
OAuth discovery, model recommendation and credential resolution.
"""
PROBE_SOURCE = r'''
import json
import sys


def discover(request):
    from hermes_cli.models import provider_model_ids
    from hermes_cli.chat_catalog import without_generation_models

    slug = request["slug"]
    source = "hermes"
    recommendation_models = None
    if request.get("use_oauth"):
        models = provider_model_ids(slug, force_refresh=True)
        source = "reference" if type(models).__name__ == "CuratedFallbackModels" else "hermes"
    else:
        from providers import get_provider_profile
        from hermes_cli.models import merge_profile_catalog

        profile = get_provider_profile(slug)
        live = profile.fetch_models(api_key=request["api_key"], base_url=request.get("base_url") or None) if profile else None
        models = merge_profile_catalog(slug, profile, live) if profile else []
        if not live:
            source = "reference"

    if slug == "nous":
        source = "account"
        from hermes_cli.auth import get_provider_auth_state
        from hermes_cli.nous_account import get_nous_portal_account_info
        from hermes_cli.models import (
            partition_nous_models_by_tier, union_with_portal_free_recommendations,
            union_with_portal_paid_recommendations,
        )
        from hermes_cli.models_pricing import get_pricing_for_provider, nous_policy_allowed_ids, restrict_to_nous_policy

        account = get_nous_portal_account_info(force_fresh=True)
        # A completed login and a failed account lookup are different states.
        # Retry only the read, once; never restart OAuth or infer paid access.
        if account.logged_in and account.error:
            account = get_nous_portal_account_info(force_fresh=True)
        if not account.logged_in or account.error or type(account.paid_service_access) is not bool:
            reason = "session_unavailable" if not account.logged_in else "account_lookup_failed" if account.error else "access_unknown"
            if account.logged_in and "certificate_verify_failed" in str(account.error).lower():
                reason = "tls_certificate_untrusted"
            return {"status": "error", "code": "plan_unknown", "reason": reason}
        free = not account.paid_service_access
        pricing = get_pricing_for_provider("nous") or {}
        portal = (get_provider_auth_state("nous") or {}).get("portal_base_url") or ""
        union = union_with_portal_free_recommendations if free else union_with_portal_paid_recommendations
        models, pricing = union(list(models), pricing, portal, force_refresh=True)
        models = restrict_to_nous_policy(models, nous_policy_allowed_ids(), rescue_empty=True)
        if free:
            if not pricing:
                return {"status": "error", "code": "plan_unknown", "reason": "pricing_unavailable"}
            models = partition_nous_models_by_tier(models, pricing, free_tier=True)[0]
            from hermes_cli import models as hermes_models
            subscription_billed = getattr(hermes_models, "_is_subscription_billed", None)
            if callable(subscription_billed):
                recommendation_models = {m for m in models if not subscription_billed(pricing.get(m))}

    models = list(dict.fromkeys(str(m) for m in without_generation_models(models or []) if m))
    recommended = ""
    if request.get("use_oauth") and source != "reference" and slug in {"nous", "openai-codex", "xai-oauth"}:
        # Reuse the tier-filtered list already checked above; a second account
        # lookup could produce a recommendation for a different access state.
        from hermes_cli import models as hermes_models
        pick_default = getattr(hermes_models, "pick_silent_default_model", None)
        if callable(pick_default):
            eligible = models if recommendation_models is None else [m for m in models if m in recommendation_models]
            preferred = pick_default(eligible, provider=slug)
            if preferred in eligible:
                recommended = preferred
    return {"status": "ok", "source": source, "recommended_model": recommended, "models": [
        {"value": m, "label": m + (" · Gratis" if m.endswith((":free", "-free")) else "")}
        for m in models]}


def check_response(request):
    from agent.auxiliary_client import resolve_provider_client

    # Resolve only the selected provider, without the auxiliary fallback ladder.
    # A single fixed prompt, no agent, tools, memory, conversation or user content.
    try:
        client, model = resolve_provider_client(request["slug"], model=request["model"])
        if not client or not model:
            return {"status": "error", "code": "missing_oauth"}
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Responde solo: Hola."}],
            max_tokens=128, timeout=15,
        )
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return {"status": "ok"}
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        code = {401: "missing_oauth", 402: "quota", 403: "forbidden", 404: "model_unavailable",
                429: "quota"}.get(status, "no_response")
        return {"status": "error", "code": code}
    return {"status": "error", "code": "no_response"}


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = check_response(request) if request.get("probe_response") else discover(request)
    except Exception:
        # Provider exceptions can contain URLs, headers or credentials. Never echo them.
        result = {"status": "error", "code": "catalog_unavailable"}
    print("IRIS_PROVIDER_MODELS=" + json.dumps(result, ensure_ascii=False))
'''
