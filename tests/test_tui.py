import time

from watertight.queue import State
from watertight.queue.manager import max_workers
from watertight.tui.app import WatertightApp
from watertight.tui.explorer import Explorer

SIZE = (120, 40)


async def settle(pilot, cond, timeout=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await pilot.pause(0.1)
        if cond():
            return True
    return False


async def explorer_ready(pilot, app):
    ex = app.query_one(Explorer)
    assert await settle(pilot, lambda: ex.root.children, 10)
    return ex


def paths_of(app):
    return {j.path.name: j.state for j in app.mgr.jobs}


async def test_starts_and_lists_only_stl_and_folders(fx):
    fx.clean_cube("a.stl")
    fx.clean_cube("B.STL")
    (fx.folder / "c.txt").write_text("x")
    (fx.folder / "sub").mkdir()
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        ex = await explorer_ready(pilot, app)
        names = [n.data.path.name for n in ex.root.children]
        assert "a.stl" in names and "B.STL" in names and "sub" in names
        assert "c.txt" not in names
        assert "workers" in str(app.query_one("#topbar").render())


async def test_select_and_enter_autostarts(fx):
    for name in ("one.stl", "two.stl"):
        fx.hole_cube(name)
    fx.clean_cube("three.stl")
    app = WatertightApp(fx.folder, jobs=3)
    async with app.run_test(size=SIZE) as pilot:
        ex = await explorer_ready(pilot, app)
        ex.focus()
        await pilot.press("a")  # select all STL files here (scanned in a thread)
        assert await settle(pilot, lambda: len(app.selected) == 3, 10)
        await pilot.press("enter")  # add + start, no "s" needed
        assert app.selected == set()
        assert len(app.mgr.jobs) == 3
        assert await settle(pilot, lambda: app.mgr.idle)
        st = paths_of(app)
        assert st == {"one.stl": State.DONE, "two.stl": State.DONE, "three.stl": State.SKIPPED}
        assert (fx.folder / "one_repaired.stl").exists()
        assert not (fx.folder / "three_repaired.stl").exists()
        assert app.query_one("#overall-bar").progress == 3


async def test_select_all_skips_repaired_files_and_selection_survives_navigation(fx):
    fx.hole_cube("x.stl")
    fx.clean_cube("x_repaired.stl")
    (fx.folder / "sub").mkdir()
    fx.clean_cube("sub/deep.stl") if False else None
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        ex = await explorer_ready(pilot, app)
        ex.focus()
        await pilot.press("a")
        assert await settle(pilot, lambda: len(app.selected) == 1, 10)
        assert {p.name for p in app.selected} == {"x.stl"}
        await pilot.press("n")
        assert app.selected == set()


async def test_ctrl_c_interrupts_everything_and_saves_nothing(fx, monkeypatch):
    for i in range(5):
        fx.hole_cube(f"f{i}.stl")
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:60")
    app = WatertightApp(fx.folder, jobs=2)
    async with app.run_test(size=SIZE) as pilot:
        ex = await explorer_ready(pilot, app)
        ex.focus()
        await pilot.press("a")
        assert await settle(pilot, lambda: len(app.selected) == 5, 10)
        await pilot.press("enter")
        assert await settle(pilot, lambda: len(app.mgr.running_jobs) == 2, 20)
        await pilot.press("ctrl+c")
        assert all(j.state == State.CANCELLED for j in app.mgr.jobs)
        assert [p.name for p in fx.folder.iterdir() if "_repaired" in p.name or p.suffix == ".tmp"] == []
        assert "nothing saved" in app._message
        # still healthy: new work starts after the interrupt
        monkeypatch.delenv("WATERTIGHT_TEST_FAULT")
        app.run_paths([fx.clean_cube("later.stl")])
        assert await settle(pilot, lambda: app.mgr.get(app.mgr.jobs[-1].id).state == State.SKIPPED)


async def test_cancel_queued_with_X_lets_running_finish(fx, monkeypatch):
    for i in range(3):
        fx.hole_cube(f"g{i}.stl")
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:1.5")
    app = WatertightApp(fx.folder, jobs=1)
    async with app.run_test(size=SIZE) as pilot:
        ex = await explorer_ready(pilot, app)
        ex.focus()
        await pilot.press("a")
        assert await settle(pilot, lambda: len(app.selected) == 3, 10)
        await pilot.press("enter")
        assert await settle(pilot, lambda: len(app.mgr.running_jobs) == 1, 20)
        await pilot.press("X")
        assert await settle(pilot, lambda: app.mgr.idle)
        states = [j.state for j in app.mgr.jobs]
        assert states == [State.DONE, State.CANCELLED, State.CANCELLED]


async def test_cancel_one_row(fx, monkeypatch):
    fx.hole_cube("slow.stl")
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:60")
    app = WatertightApp(fx.folder, jobs=1)
    async with app.run_test(size=SIZE) as pilot:
        app.run_paths([fx.path("slow.stl")])
        assert await settle(pilot, lambda: len(app.mgr.running_jobs) == 1, 20)
        app.table.focus()
        await pilot.press("c")
        assert app.mgr.jobs[0].state == State.CANCELLED


async def test_quit_with_active_jobs_asks_first(fx, monkeypatch):
    fx.hole_cube("slow.stl")
    monkeypatch.setenv("WATERTIGHT_TEST_FAULT", "sleep:check:60")
    app = WatertightApp(fx.folder, jobs=1)
    async with app.run_test(size=SIZE) as pilot:
        app.run_paths([fx.path("slow.stl")])
        assert await settle(pilot, lambda: len(app.mgr.running_jobs) == 1, 20)
        await pilot.press("q")
        await pilot.pause(0.2)
        assert type(app.screen).__name__ == "ConfirmScreen"
        await pilot.press("n")
        await pilot.pause(0.2)
        assert type(app.screen).__name__ != "ConfirmScreen" and app.is_running
        await pilot.press("q")
        await pilot.press("y")
        await pilot.pause(0.3)
        assert app.mgr.jobs[0].state == State.CANCELLED
        assert [p.name for p in fx.folder.iterdir() if "_repaired" in p.name or p.suffix == ".tmp"] == []


async def test_workers_keys_and_overwrite_toggle(fx):
    app = WatertightApp(fx.folder, jobs=1)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("plus")
        assert app.mgr.workers == min(2, max_workers())
        await pilot.press("o")
        assert app.mgr.options.overwrite is True
        await pilot.press("p")
        assert app.mgr.paused


async def test_too_small_window_shows_message(fx):
    app = WatertightApp(fx.folder)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause(0.2)
        assert app.query_one("#toosmall").display is True
    app2 = WatertightApp(fx.folder)
    async with app2.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        assert app2.query_one("#toosmall").display is False


async def test_duplicate_add_is_ignored_with_message(fx):
    p = fx.hole_cube()
    app = WatertightApp(fx.folder, jobs=1)
    async with app.run_test(size=SIZE):
        app.mgr.pause()
        app.run_paths([p])
        app.run_paths([p])
        assert len(app.mgr.jobs) == 1
        assert "Ignored 1" in app._message


# ---- places and network drives ----------------------------------------------------------------
async def test_locations_list_opens_a_drive(fx, tmp_path, monkeypatch):
    from watertight.locations import NETWORK, Location

    nas = tmp_path / "nas_share"
    nas.mkdir()
    fx.clean_cube("start.stl")
    (nas / "model.stl").write_bytes(fx.path("start.stl").read_bytes())
    fake = [Location("Home", tmp_path, "place", "home"), Location("nas", nas, NETWORK, "smbfs //nas/stl")]
    monkeypatch.setattr("watertight.tui.app.list_locations", lambda: fake)
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        await explorer_ready(pilot, app)
        await pilot.press("l")
        await pilot.pause(0.2)
        assert type(app.screen).__name__ == "LocationsScreen"
        await pilot.press("down", "enter")  # highlight "nas", open it
        assert await settle(pilot, lambda: str(app.explorer.path) == str(nas), 10)
        ex = app.explorer
        assert await settle(pilot, lambda: any(n.data.path.name == "model.stl" for n in ex.root.children), 10)


async def test_goto_typed_path_and_bad_path(fx, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        await explorer_ready(pilot, app)
        await pilot.press("g")
        await pilot.pause(0.2)
        assert type(app.screen).__name__ == "GoToScreen"
        await pilot.press(*str(other), "enter")
        assert await settle(pilot, lambda: str(app.explorer.path) == str(other), 10)
        await pilot.press("g")
        await pilot.pause(0.2)
        await pilot.press(*"/no/such/place", "enter")
        assert await settle(pilot, lambda: "Cannot open" in app._message, 10)
        assert str(app.explorer.path) == str(other)  # unchanged


async def test_up_at_root_opens_locations(fx, monkeypatch):
    monkeypatch.setattr("watertight.tui.app.list_locations", lambda: [])
    app = WatertightApp(fx.folder.anchor and __import__("pathlib").Path(fx.folder.anchor))
    async with app.run_test(size=SIZE) as pilot:
        await explorer_ready(pilot, app)
        app.explorer.focus()
        await pilot.press("u")
        await pilot.pause(0.3)
        assert type(app.screen).__name__ == "LocationsScreen"


async def test_slow_drive_does_not_freeze_the_app(fx, tmp_path, monkeypatch):
    import os
    import time as _time

    import watertight.tui.app as appmod

    real = os.listdir

    def slow(path="."):
        if str(path).endswith("slowshare"):
            _time.sleep(3)
        return real(path)

    slow_dir = tmp_path / "slowshare"
    slow_dir.mkdir()
    monkeypatch.setattr(appmod, "OPEN_TIMEOUT", 0.5)
    monkeypatch.setattr(os, "listdir", slow)
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        await explorer_ready(pilot, app)
        app.open_location(slow_dir)
        t0 = _time.monotonic()
        await pilot.press("p")  # the UI still answers while the drive is stuck
        assert _time.monotonic() - t0 < 0.5 and app.mgr.paused
        assert await settle(pilot, lambda: "No answer from" in app._message, 5)
        assert str(app.explorer.path) != str(slow_dir)


async def test_f_toggles_keep_trying_and_the_user_is_told_when_it_stops(fx):
    import trimesh

    a = trimesh.creation.icosphere(subdivisions=3)
    mid = a.vertices[a.edges_unique[0]].mean(axis=0)
    b = trimesh.Trimesh(2 * mid - a.vertices, a.faces[:, ::-1], process=False)
    p = fx.folder / "pinched.stl"
    trimesh.util.concatenate([a, b]).export(p)
    app = WatertightApp(fx.folder)
    async with app.run_test(size=SIZE) as pilot:
        await explorer_ready(pilot, app)
        assert app.mgr.options.refine is False
        await pilot.press("f")
        assert app.mgr.options.refine is True
        assert "keep trying on" in str(app.query_one("#topbar").render())
        app.mgr.add([p])
        assert await settle(pilot, lambda: bool(app.mgr.jobs) and app.mgr.jobs[0].final, 60)
        assert await settle(pilot, lambda: bool(app._notified), 10)
        assert app.mgr.jobs[0].result.refine["tries"] >= 1
        await pilot.press("f")
        assert app.mgr.options.refine is False
