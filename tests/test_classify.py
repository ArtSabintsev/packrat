from ptcgl_redeem.classify import STOPPING, CodeStatus, classify_redeem, classify_verify


def test_stopping_statuses():
    assert STOPPING == {CodeStatus.CAPTCHA, CodeStatus.FATAL, CodeStatus.INDETERMINATE}


def test_verify_valid():
    status, detail = classify_verify(
        {"couponResults": [{"couponCode": "ABC123ABC1234", "validationStatus": "valid"}]},
        "ABC123ABC1234",
    )
    assert status is CodeStatus.VALID
    assert "pending" in detail


def test_verify_rejected():
    status, detail = classify_verify(
        {
            "couponResults": [
                {
                    "couponCode": "ABC123ABC1234",
                    "validationStatus": "invalid",
                    "localizedError": "already redeemed",
                }
            ]
        },
        "ABC123ABC1234",
    )
    assert status is CodeStatus.REJECTED
    assert detail == "already redeemed"


def test_verify_captcha():
    status, _ = classify_verify({"error": "recaptcha_validation_failure"}, "ABC123ABC1234")
    assert status is CodeStatus.CAPTCHA


def test_verify_missing_code_is_fatal():
    status, _ = classify_verify({"couponResults": []}, "ABC123ABC1234")
    assert status is CodeStatus.FATAL


def test_redeem_success():
    status, _ = classify_redeem(
        {"redeemCouponResults": [{"couponCode": "ABC123ABC1234", "redemptionSuccessful": True}]},
        "ABC123ABC1234",
    )
    assert status is CodeStatus.SUCCESS


def test_redeem_failure():
    status, _ = classify_redeem(
        {"redeemCouponResults": [{"couponCode": "ABC123ABC1234", "redemptionSuccessful": False}]},
        "ABC123ABC1234",
    )
    assert status is CodeStatus.REJECTED


def test_redeem_captcha_is_indeterminate():
    status, _ = classify_redeem({"error": "recaptcha_validation_failure"}, "ABC123ABC1234")
    assert status is CodeStatus.INDETERMINATE


def test_redeem_missing_code_is_indeterminate():
    status, _ = classify_redeem({"redeemCouponResults": []}, "ABC123ABC1234")
    assert status is CodeStatus.INDETERMINATE
