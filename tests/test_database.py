import tempfile, os
from memory.database import Database

def test_project_task_cycle():
    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "t.db"))
        pid = db.create_project("p", d)
        tid = db.create_task(pid, "backend_developer", "Build")
        assert tid
        claimed = db.claim_task("w1")
        assert claimed and claimed["id"] == tid
        db.set_task_status(tid, "COMPLETED", result="ok")
        assert db.list_tasks(pid)[0]["status"] == "COMPLETED"
        db.close()

def test_dependencies_block_claim():
    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "t.db"))
        pid = db.create_project("p", d)
        t1 = db.create_task(pid, "requirement_analyst", "A")
        t2 = db.create_task(pid, "backend_developer", "B", depends_on=[t1])
        c1 = db.claim_task("w1")
        assert c1["id"] == t1
        # t2 blocked until t1 completes
        assert db.claim_task("w2") is None
        db.set_task_status(t1, "COMPLETED")
        c2 = db.claim_task("w2")
        assert c2["id"] == t2
        db.close()

def test_recover():
    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "t.db"))
        pid = db.create_project("p", d)
        tid = db.create_task(pid, "qa_tester", "T")
        db.claim_task("w1")
        assert db.recover_interrupted() == 1
        assert db.list_tasks(pid)[0]["status"] == "QUEUED"
        db.close()
