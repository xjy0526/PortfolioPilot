"""Tests für das Knowledge Data Module."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from services.knowledge_data import (
    DAILY_TIPS,
    PROJECT_KNOWLEDGE,
    get_daily_tip,
    get_project_summary,
    get_all_technologies,
    get_projects_overview,
    _fuzzy_match_project,
)


class TestDailyTips:
    """Tests für die täglichen Tipps."""

    def test_tips_not_empty(self):
        assert len(DAILY_TIPS) > 0

    def test_tip_structure(self):
        """Jeder Tipp hat die erforderlichen Keys."""
        for i, tip in enumerate(DAILY_TIPS):
            assert "category" in tip, f"Tipp {i} fehlt 'category'"
            assert "project" in tip, f"Tipp {i} fehlt 'project'"
            assert "title" in tip, f"Tipp {i} fehlt 'title'"
            assert "text" in tip, f"Tipp {i} fehlt 'text'"

    def test_tip_project_valid(self):
        """Jeder Tipp referenziert ein existierendes Projekt."""
        for tip in DAILY_TIPS:
            assert tip["project"] in PROJECT_KNOWLEDGE, (
                f"Tipp '{tip['title']}' referenziert unbekanntes Projekt '{tip['project']}'"
            )

    def test_get_daily_tip_returns_dict(self):
        tip = get_daily_tip()
        assert isinstance(tip, dict)
        assert "title" in tip
        assert "text" in tip

    def test_daily_tip_rotation(self):
        """Verschiedene Tage geben verschiedene Tipps (bei genug Tipps)."""
        tip_0 = get_daily_tip(0)
        tip_1 = get_daily_tip(1)
        # Sollte verschieden sein (sofern > 1 Tipp)
        if len(DAILY_TIPS) > 1:
            assert tip_0["title"] != tip_1["title"]

    def test_daily_tip_wraps_around(self):
        """Nach N Tagen wiederholt sich der Zyklus."""
        n = len(DAILY_TIPS)
        tip_0 = get_daily_tip(0)
        tip_n = get_daily_tip(n)
        assert tip_0["title"] == tip_n["title"]

    def test_tip_text_not_empty(self):
        """Kein Tipp hat leeren Text."""
        for tip in DAILY_TIPS:
            assert len(tip["text"].strip()) > 10, f"Tipp '{tip['title']}' hat zu kurzen Text"


class TestProjectKnowledge:
    """Tests für die Projekt-Datenbank."""

    def test_all_projects_present(self):
        expected = {"ai_knowledge", "pokerpro", "portfoliopilot", "job_automation"}
        assert set(PROJECT_KNOWLEDGE.keys()) == expected

    def test_project_structure(self):
        required_keys = {"name", "emoji", "description", "difficulty", "technologies", "best_practices", "key_learning"}
        for key, project in PROJECT_KNOWLEDGE.items():
            for rk in required_keys:
                assert rk in project, f"Projekt '{key}' fehlt '{rk}'"

    def test_technologies_not_empty(self):
        for key, project in PROJECT_KNOWLEDGE.items():
            assert len(project["technologies"]) > 0, f"Projekt '{key}' hat keine Technologien"

    def test_best_practices_not_empty(self):
        for key, project in PROJECT_KNOWLEDGE.items():
            assert len(project["best_practices"]) > 0, f"Projekt '{key}' hat keine Best Practices"


class TestGetProjectSummary:
    """Tests für get_project_summary()."""

    def test_valid_project(self):
        summary = get_project_summary("portfoliopilot")
        assert "PortfolioPilot" in summary
        assert "Technologien" in summary
        assert "Best Practices" in summary

    def test_all_projects_return_content(self):
        for key in PROJECT_KNOWLEDGE:
            summary = get_project_summary(key)
            assert len(summary) > 50, f"Summary für '{key}' ist zu kurz"

    def test_unknown_project(self):
        summary = get_project_summary("unbekannt")
        assert "Unbekanntes Projekt" in summary
        assert "Verfügbar" in summary

    def test_fuzzy_match_alias(self):
        """Aliases wie 'poker' oder 'aktien' funktionieren."""
        assert "PokerPro" in get_project_summary("poker")
        assert "PortfolioPilot" in get_project_summary("aktien")
        assert "AI Knowledge" in get_project_summary("quiz")


class TestFuzzyMatch:
    """Tests für die Fuzzy-Matching-Logik."""

    def test_exact_match(self):
        assert _fuzzy_match_project("portfoliopilot") == "portfoliopilot"
        assert _fuzzy_match_project("pokerpro") == "pokerpro"

    def test_alias_match(self):
        assert _fuzzy_match_project("poker") == "pokerpro"
        assert _fuzzy_match_project("finanz") == "portfoliopilot"
        assert _fuzzy_match_project("job") == "job_automation"
        assert _fuzzy_match_project("ai") == "ai_knowledge"

    def test_case_insensitive(self):
        assert _fuzzy_match_project("PortfolioPilot") == "portfoliopilot"
        assert _fuzzy_match_project("POKERPRO") == "pokerpro"

    def test_unknown_returns_none(self):
        assert _fuzzy_match_project("unbekannt") is None
        assert _fuzzy_match_project("xyz123") is None


class TestGetAllTechnologies:
    """Tests für get_all_technologies()."""

    def test_returns_list(self):
        techs = get_all_technologies()
        assert isinstance(techs, list)

    def test_contains_known_techs(self):
        techs = get_all_technologies()
        assert "Python" in techs
        assert "FastAPI" in techs
        assert "HTML5" in techs
        assert "Docker" in techs

    def test_no_duplicates(self):
        techs = get_all_technologies()
        assert len(techs) == len(set(techs))


class TestGetProjectsOverview:
    """Tests für get_projects_overview()."""

    def test_returns_string(self):
        overview = get_projects_overview()
        assert isinstance(overview, str)

    def test_contains_all_projects(self):
        overview = get_projects_overview()
        for project in PROJECT_KNOWLEDGE.values():
            assert project["name"] in overview
