import json

import pytest

import config
from tools import report_tool as rt
from utils.resilience import ToolError


def company(name: str) -> str:
    return (f"## {name}\n\n### 1. Povestea pe șleau\nFirma e o mașinărie de făcut bani. " + "x" * 120 +
            "\n\n### 2. De ce cifrele arată beton\nFCF-ul curge ca o chirie bună.\n\n"
            "### 3. Testul meu de Paranoia (Ce am săpat pe net)\nNimic grav pe GitHub sau Reddit.\n\n")


GRAVEYARD = "## Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)\n- INTC: arde bani.\n"


def report(n: int, graveyard: bool = True) -> str:
    return "# Raportul Vulpii\n\n" + "".join(company(f"Firma {i}") for i in range(n)) + (GRAVEYARD if graveyard else "")


@pytest.fixture
def tmp_report(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_PATH", tmp_path / "raport_sateliti.html")
    rt.configure(run_dir=tmp_path / "run1", model="claude-test")
    yield tmp_path
    rt.configure(run_dir=None)


def test_check_report_counts_companies():
    assert rt.check_report(report(4))["companies"] == 4


def test_check_report_tolerates_missing_diacritics():
    text = report(3).replace("șleau", "sleau").replace("arată", "arata")
    assert rt.check_report(text)["companies"] == 3


@pytest.mark.parametrize("text, fragment", [
    ("", "prea scurt"),
    (report(2), "doar 2 companii"),
    (report(6), "prea multe"),
    (report(3, graveyard=False), "Cimitirul Iluziilor"),
])
def test_check_report_rejects(text, fragment):
    with pytest.raises(ToolError) as exc:
        rt.check_report(text)
    assert fragment in str(exc.value)


def test_tool_writes_file_with_header_and_disclaimer(tmp_report):
    out = json.loads(rt.generate_client_report(report(3)))
    assert out["ok"] and out["companies"] == 3
    saved = config.REPORT_PATH.read_text(encoding="utf-8")
    assert "Paranoid Wolf" in saved and "claude-test" in saved
    assert "Nu este o recomandare de investiții" in saved
    assert "o mașinărie de făcut bani" in saved  # UTF-8 păstrat
    assert saved.startswith("<!doctype html>") and 'id="jurnal"' in saved
    assert (tmp_report / "run1" / "raport_sateliti.html").read_text(encoding="utf-8") == saved


def test_tool_invalid_report_does_not_write(tmp_report):
    out = json.loads(rt.generate_client_report(report(1) + "y" * 600))
    assert out["ok"] is False and "doar 1 companii" in out["error"]
    assert "retrimite" in out["hint"].lower()
    assert not config.REPORT_PATH.exists()
