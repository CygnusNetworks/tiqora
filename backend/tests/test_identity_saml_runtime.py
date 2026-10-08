import pytest

import tiqora.identity.saml_runtime as rt


def test_missing_binary_reports_xmlsec1(monkeypatch: pytest.MonkeyPatch) -> None:
    rt.saml_availability.cache_clear()
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    monkeypatch.setattr(rt, "_package_present", lambda: True)
    av = rt.saml_availability()
    assert av.available is False and av.missing == "xmlsec1"
    rt.saml_availability.cache_clear()


def test_missing_package_reports_package(monkeypatch: pytest.MonkeyPatch) -> None:
    rt.saml_availability.cache_clear()
    monkeypatch.setattr(rt, "_package_present", lambda: False)
    assert rt.saml_availability().missing == "package"
    rt.saml_availability.cache_clear()


def test_available_reports_binary_path(monkeypatch: pytest.MonkeyPatch) -> None:
    rt.saml_availability.cache_clear()
    monkeypatch.setattr(rt.shutil, "which", lambda name: "/usr/bin/xmlsec1")
    monkeypatch.setattr(rt, "_package_present", lambda: True)
    av = rt.saml_availability()
    assert av.available is True and av.missing is None and av.xmlsec_binary == "/usr/bin/xmlsec1"
    rt.saml_availability.cache_clear()


def test_import_saml2_raises_unavailable_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "_package_present", lambda: False)
    with pytest.raises(rt.SamlUnavailable):
        rt.import_saml2()


def test_require_saml_fails_hard_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from _saml_idp import require_saml

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("TIQORA_REQUIRE_SAML_TESTS", "1")
    with pytest.raises(pytest.fail.Exception, match="xmlsec1 binary missing"):
        require_saml()
    monkeypatch.delenv("TIQORA_REQUIRE_SAML_TESTS")
    with pytest.raises(pytest.skip.Exception, match="xmlsec1 binary missing"):
        require_saml()
