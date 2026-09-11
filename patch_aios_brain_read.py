# --- 1. services/aios_service.py: get_brain_graph function ---
p = "services/aios_service.py"
s = open(p).read()

old_anchor = '''def get_overview(db: Session, user_id: int) -> Dict[str, Any]:'''
new_anchor = '''def get_brain_graph(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Returns the user's AIOS Brain graph - entities and relationships -
    for visualization. Only is_user_owned entities are returned as
    top-level "nodes"; supporting concept knowledge (is_user_owned=False)
    is attached as each node's "description" instead of appearing as its
    own visible node, per the "two kinds of knowledge" rule - concept
    entities support understanding, they are not themselves memories to
    show the user.
    """
    entities = (
        db.query(AiosEntity)
        .filter(AiosEntity.user_id == user_id, AiosEntity.status == "active")
        .order_by(desc(AiosEntity.updated_at))
        .all()
    )
    relationships = (
        db.query(AiosRelationship)
        .filter(AiosRelationship.user_id == user_id, AiosRelationship.status == "active")
        .order_by(desc(AiosRelationship.updated_at))
        .all()
    )

    entity_by_id = {e.id: e for e in entities}
    user_owned_ids = {e.id for e in entities if e.is_user_owned}

    nodes = [
        {
            "id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "description": e.description,
            "confidence": e.confidence,
        }
        for e in entities
        if e.is_user_owned
    ]

    edges = []
    for r in relationships:
        # Only surface edges where the "to" side is a user-owned node -
        # an edge pointing at pure supporting-concept knowledge isn't
        # something to visualize as part of the user's own graph.
        if r.to_entity_id not in user_owned_ids:
            continue
        to_entity = entity_by_id.get(r.to_entity_id)
        if not to_entity:
            continue
        from_entity = entity_by_id.get(r.from_entity_id) if r.from_entity_id else None
        edges.append({
            "id": r.id,
            "from": from_entity.name if from_entity else "user",
            "from_id": r.from_entity_id,
            "relationship_type": r.relationship_type,
            "to": to_entity.name,
            "to_id": r.to_entity_id,
            "confidence": r.confidence,
            "temporal_state": r.temporal_state,
        })

    return {"nodes": nodes, "edges": edges}


def get_overview(db: Session, user_id: int) -> Dict[str, Any]:'''
assert s.count(old_anchor) == 1, "get_overview anchor not found/not unique"
s = s.replace(old_anchor, new_anchor, 1)

open(p, "w").write(s)
print("aios_service.py patched")


# --- 2. main.py: GET /aios/brain route ---
p = "main.py"
s = open(p).read()

old_route = '''@app.get("/aios/overview")
def aios_overview(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        _require_aios_access(user_id)  # plan/license_id unused here - no metering on read/write-only AIOS routes
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/overview: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:'''

new_route = '''@app.get("/aios/overview")
def aios_overview(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        _require_aios_access(user_id)  # plan/license_id unused here - no metering on read/write-only AIOS routes
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/overview: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:
            db.close()


@app.get("/aios/brain")
def aios_brain(authorization: str = AiosHeader(default="")):
    db = None
    try:
        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.get_brain_graph(db, user_id)
        return {"success": True, **result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[AIOS] Unexpected error in /aios/brain: {e}")
        return {"success": False, "error": "Something went wrong. Please try again."}
    finally:
        if db is not None:'''

assert s.count(old_route) == 1, "aios_overview route anchor not found/not unique"
s = s.replace(old_route, new_route, 1)

open(p, "w").write(s)
print("main.py patched")
