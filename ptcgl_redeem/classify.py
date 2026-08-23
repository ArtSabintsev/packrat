from __future__ import annotations

from enum import StrEnum


class CodeStatus(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    VALID = "valid"  # verified, waiting for redeem click
    VALID_NOT_REDEEMED = "valid_not_redeemed"  # dry-run
    CAPTCHA = "captcha"
    INDETERMINATE = "indeterminate"
    TRANSIENT = "transient"
    FATAL = "fatal"
    NOT_ATTEMPTED = "not_attempted"


STOPPING = {CodeStatus.CAPTCHA, CodeStatus.FATAL, CodeStatus.INDETERMINATE}


def classify_verify(payload: dict, code: str) -> tuple[CodeStatus, str]:
    if payload.get("error") == "recaptcha_validation_failure":
        return CodeStatus.CAPTCHA, payload.get("localizedError") or "reCAPTCHA rejected the request"

    for entry in payload.get("couponResults") or []:
        if entry.get("couponCode") != code:
            continue
        if entry.get("validationStatus") == "valid":
            return CodeStatus.VALID, "verified, pending redeem"
        reason = entry.get("localizedError") or str(entry.get("validationStatus"))
        return CodeStatus.REJECTED, reason

    return CodeStatus.FATAL, "code missing from verify response"


def classify_redeem(payload: dict, code: str) -> tuple[CodeStatus, str]:
    if payload.get("error") == "recaptcha_validation_failure":
        msg = payload.get("localizedError") or "reCAPTCHA failed on redeem"
        return CodeStatus.INDETERMINATE, msg

    for entry in payload.get("redeemCouponResults") or []:
        if entry.get("couponCode") != code:
            continue
        ok = entry.get("redemptionSuccessful")
        if ok is True:
            return CodeStatus.SUCCESS, "redeemed"
        if ok is False:
            return CodeStatus.REJECTED, entry.get("localizedError") or "redemption not successful"
        return CodeStatus.INDETERMINATE, "redemptionSuccessful missing"

    return CodeStatus.INDETERMINATE, "code missing from redeem response"
