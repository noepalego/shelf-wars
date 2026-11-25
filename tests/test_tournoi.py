import json

import pytest

from simarche import tournoi


def test_plan_apparie_des_deux_cotes():
    jobs = list(tournoi._jobs([7], weeks=12, households=30))
    for scenario in tournoi.SCENARIOS:
        selected = [job for job in jobs if job.scenario == scenario]
        benchmark = [j for j in selected if j.opponent == "temoin"]
        assert len(benchmark) == 1 + 2 * (len(tournoi.POLICIES) - 1)
        assert sum(job.policy == "temoin" for job in benchmark) == 1
        assert len([j for j in selected if j.policy == j.opponent == "adaptatif"]) == 1
        for policy in set(tournoi.POLICIES) - {"temoin"}:
            assert sorted(j.side for j in benchmark if j.policy == policy) == ["A", "B"]
    assert not tournoi._events("calme", 12)
    assert [e["semaine"] for e in tournoi._events("choc", 12)] == [5, 9]
    assert not tournoi._events("choc", 4)


def test_ecarts_et_victoires_par_population():
    runs = []
    for seed, differences in [(7, [20, -5]), (8, [2, -7])]:
        for side, delta, baseline in zip("AB", differences, [100, 500]):
            for policy, diff in [("temoin", 0), ("rayon", delta)]:
                weeks = [{"profit": 10, "visits": 4, "waste": 2, "coupons": 1},
                         {"profit": baseline + diff, "visits": 8 + int(bool(diff)), "waste": 4, "coupons": 2}]
                runs.append({"scenario": "calme", "policy": policy, "seed": seed, "side": side, "weeks": weeks})
    summary = {row["policy"]: row for row in tournoi._summary(runs)}
    assert summary["temoin"]["deltaProfit"] == 0
    assert summary["rayon"] == {"scenario": "calme", "policy": "rayon", "profit": 302.5,
                                "deltaProfit": 2.5, "deltaVisits": 1, "deltaWaste": 0,
                                "deltaCoupons": 0, "seedWins": 1, "seeds": 2}


def test_export_et_reproductibilite(tmp_path, monkeypatch):
    game = tournoi.Game("calme", 42, "temoin", "A", 2, 8)
    duel = tournoi.Game("calme", 42, "adaptatif", "A", 2, 8, opponent="adaptatif")
    monkeypatch.setattr(tournoi, "_jobs", lambda *args: [game, duel])
    target = tmp_path / "site" / "resultats.js"
    first = tournoi.run_tournament(target, seeds=[42], weeks=2, households=8)
    exported = json.loads(target.read_text(encoding="utf-8").removeprefix("window.SIMARCHE=").removesuffix(";\n"))
    assert exported == first == tournoi.run_tournament(target, seeds=[42], weeks=2, households=8)
    assert {r["side"] for r in first["runs"]} == {"A", "B"}
    assert {r["side"] for r in first["duels"]} == {"A", "B"}
    assert first["meta"]["simulations"] == 2 and first["meta"]["duelSimulations"] == 1
    for run in first["runs"]:
        assert [w["week"] for w in run["weeks"]] == [1, 2]
        assert all(w["arm"] == "temoin" and w["treated"] == 0 for w in run["weeks"])
    for run in first["runs"] + first["duels"]:
        assert all(w["grossMargin"] <= w["revenue"] and w["labelCost"] >= 0 for w in run["weeks"])
    with pytest.raises(ValueError):
        tournoi.run_tournament(target, seeds=[42, 42])
    assert target.read_text(encoding="utf-8").startswith("window.SIMARCHE=")
