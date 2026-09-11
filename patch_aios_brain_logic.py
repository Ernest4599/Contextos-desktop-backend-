p = "services/aios_service.py"
s = open(p).read()

# 1. Add entity-type vocabulary near the other constants
old_const = '''ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_TEMPORAL_STATES = {"permanent", "current", "temporary", "historical", "unknown"}'''
new_const = '''ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_TEMPORAL_STATES = {"permanent", "current", "temporary", "historical", "unknown"}
ALLOWED_ENTITY_TYPES = {"person", "organization", "product", "platform", "project", "concept", "other"}
MAX_EXISTING_ENTITIES_IN_PROMPT = 60'''
assert s.count(old_const) == 1, "constants anchor not found/not unique"
s = s.replace(old_const, new_const, 1)

# 2. Extend CLASSIFY_SYSTEM_PROMPT to also extract entities/relationships
old_prompt = '''Respond with ONLY a JSON object of this exact shape:
{"items": [{"content": "...", "category": "...", "action": "new", "confidence": "high", "temporal_state": "permanent"}, {"content": "...", "category": "...", "action": "update", "existing_id": 12, "confidence": "high", "temporal_state": "current"}]}

If nothing useful is present, return {"items": []}. No preamble, no markdown fences."""'''
new_prompt = '''6. Separately, identify any ENTITIES worth remembering as part of the Brain graph - people, organizations, products, platforms, projects, or concepts the user mentioned. For each entity:
   - "name": a short canonical name (e.g. "TechBit", "Instagram", "Software Engineer")
   - "entity_type": one of person, organization, product, platform, project, concept, other
   - "is_user_owned": true if this is a fact ABOUT the user (something they own, do, or are), false if it's supporting knowledge AIOS needs TO UNDERSTAND the statement (e.g. what "Instagram" generally is) - see EXISTING ENTITIES below for entities AIOS already understands
   - "description": ONLY for is_user_owned=false entities where AIOS doesn't already have one below - a short (1 sentence) explanation of what the concept/entity is, just enough to understand this statement, not a deep research writeup. Omit or leave empty for is_user_owned=true entities and for anything already in EXISTING ENTITIES.
   - "confidence": same high/medium/low rule as above
   Do NOT create an entity for something only mentioned in passing with no lasting relevance. Do NOT recreate an entity already listed in EXISTING ENTITIES below - reuse its exact existing name instead.
7. Identify RELATIONSHIPS between the user (or between two entities) and an entity. For each relationship:
   - "from": either the literal string "user", or the exact name of an entity from this same response's "entities" list or from EXISTING ENTITIES
   - "relationship_type": a short verb phrase (e.g. "occupation", "runs", "platform", "topic", "age")
   - "to": the exact name of an entity from this same response's "entities" list or from EXISTING ENTITIES
   - "confidence" and "temporal_state": same rules as above
   Only create relationships that are actually meaningful connections, not every possible pairing.

Respond with ONLY a JSON object of this exact shape:
{"items": [{"content": "...", "category": "...", "action": "new", "confidence": "high", "temporal_state": "permanent"}, {"content": "...", "category": "...", "action": "update", "existing_id": 12, "confidence": "high", "temporal_state": "current"}], "entities": [{"name": "...", "entity_type": "...", "is_user_owned": true, "description": "", "confidence": "high"}], "relationships": [{"from": "user", "relationship_type": "occupation", "to": "...", "confidence": "high", "temporal_state": "permanent"}]}

If nothing useful is present, return {"items": [], "entities": [], "relationships": []}. No preamble, no markdown fences."""'''
assert s.count(old_prompt) == 1, "CLASSIFY_SYSTEM_PROMPT ending anchor not found/not unique"
s = s.replace(old_prompt, new_prompt, 1)

# 3. Add a helper to format existing entities for the prompt, next to _format_existing_memories
old_helper = '''def _format_existing_memories(memories: List[AiosMemory]) -> str:
    if not memories:
        return "(none yet)"
    lines = [f'id={m.id} category={m.category} content="{decrypt_text(m.content)}"' for m in memories]
    return "\\n".join(lines)'''
new_helper = '''def _format_existing_memories(memories: List[AiosMemory]) -> str:
    if not memories:
        return "(none yet)"
    lines = [f'id={m.id} category={m.category} content="{decrypt_text(m.content)}"' for m in memories]
    return "\\n".join(lines)


def _format_existing_entities(entities: List["AiosEntity"]) -> str:
    if not entities:
        return "(none yet)"
    lines = []
    for e in entities:
        desc = f' description="{e.description}"' if e.description else ""
        lines.append(f'name="{e.name}" type={e.entity_type} is_user_owned={e.is_user_owned}{desc}')
    return "\\n".join(lines)'''
assert s.count(old_helper) == 1, "_format_existing_memories anchor not found/not unique"
s = s.replace(old_helper, new_helper, 1)

# 4. tell_aios: fetch existing entities, include in prompt, resolve entities/relationships after items
old_import_models = "from services.models import AiosMemory"
new_import_models = "from services.models import AiosMemory, AiosEntity, AiosRelationship"
assert s.count(old_import_models) == 1, "AiosMemory import anchor not found/not unique"
s = s.replace(old_import_models, new_import_models, 1)

old_fetch = '''    existing = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(MAX_EXISTING_MEMORIES_IN_PROMPT)
        .all()
    )

    user_content = (
        f"NEW INPUT:\\n{raw_input}\\n\\n"
        f"EXISTING MEMORIES:\\n{_format_existing_memories(existing)}"
    )'''
new_fetch = '''    existing = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(MAX_EXISTING_MEMORIES_IN_PROMPT)
        .all()
    )

    existing_entities = (
        db.query(AiosEntity)
        .filter(AiosEntity.user_id == user_id, AiosEntity.status == "active")
        .order_by(desc(AiosEntity.updated_at))
        .limit(MAX_EXISTING_ENTITIES_IN_PROMPT)
        .all()
    )

    user_content = (
        f"NEW INPUT:\\n{raw_input}\\n\\n"
        f"EXISTING MEMORIES:\\n{_format_existing_memories(existing)}\\n\\n"
        f"EXISTING ENTITIES:\\n{_format_existing_entities(existing_entities)}"
    )'''
assert s.count(old_fetch) == 1, "existing memories fetch anchor not found/not unique"
s = s.replace(old_fetch, new_fetch, 1)

old_return = '''    db.commit()

    return {
        "added": added,
        "updated": updated,
        "conflicts": conflicts,
        "duplicates_skipped": skipped_duplicates,
    }'''
new_return = '''    # --- Resolve entities and relationships (AIOS Brain graph) ---
    entities_raw = parsed.get("entities", [])
    if not isinstance(entities_raw, list):
        entities_raw = []
    relationships_raw = parsed.get("relationships", [])
    if not isinstance(relationships_raw, list):
        relationships_raw = []

    # name -> AiosEntity, seeded with what already existed, extended as new ones are created this call
    entity_by_name = {e.name: e for e in existing_entities}
    entities_created = 0

    for ent in entities_raw:
        name = (ent.get("name") or "").strip()
        entity_type = ent.get("entity_type") if ent.get("entity_type") in ALLOWED_ENTITY_TYPES else "other"
        is_user_owned = bool(ent.get("is_user_owned", True))
        description = (ent.get("description") or "").strip() or None
        ent_confidence = ent.get("confidence") if ent.get("confidence") in ALLOWED_CONFIDENCE else "medium"

        if not name:
            continue
        if name in entity_by_name:
            # Reinforce - bump updated_at via a no-op field touch, keep existing description/type as-is
            existing_ent = entity_by_name[name]
            existing_ent.updated_at = now
            db.add(existing_ent)
            continue

        new_entity = AiosEntity(
            user_id=user_id, entity_type=entity_type, name=name, description=description,
            is_user_owned=is_user_owned, confidence=ent_confidence, source="user_input", status="active",
        )
        db.add(new_entity)
        db.flush()  # assign new_entity.id so relationships below can reference it
        entity_by_name[name] = new_entity
        entities_created += 1

    relationships_created = 0

    for rel in relationships_raw:
        from_name = (rel.get("from") or "").strip()
        to_name = (rel.get("to") or "").strip()
        rel_type = (rel.get("relationship_type") or "").strip()
        rel_confidence = rel.get("confidence") if rel.get("confidence") in ALLOWED_CONFIDENCE else "medium"
        rel_temporal = rel.get("temporal_state") if rel.get("temporal_state") in ALLOWED_TEMPORAL_STATES else "unknown"

        if not to_name or not rel_type:
            continue

        to_entity = entity_by_name.get(to_name)
        if not to_entity:
            continue  # "to" must reference an entity that exists or was just created above

        from_entity_id = None
        if from_name and from_name.lower() != "user":
            from_entity = entity_by_name.get(from_name)
            if not from_entity:
                continue
            from_entity_id = from_entity.id

        existing_rel = (
            db.query(AiosRelationship)
            .filter(
                AiosRelationship.user_id == user_id,
                AiosRelationship.from_entity_id == from_entity_id,
                AiosRelationship.relationship_type == rel_type,
                AiosRelationship.to_entity_id == to_entity.id,
                AiosRelationship.status == "active",
            )
            .first()
        )
        if existing_rel:
            existing_rel.updated_at = now
            db.add(existing_rel)
            continue

        new_relationship = AiosRelationship(
            user_id=user_id, from_entity_id=from_entity_id, relationship_type=rel_type,
            to_entity_id=to_entity.id, confidence=rel_confidence, temporal_state=rel_temporal,
            status="active", source="user_input",
        )
        db.add(new_relationship)
        relationships_created += 1

    db.commit()

    return {
        "added": added,
        "updated": updated,
        "conflicts": conflicts,
        "duplicates_skipped": skipped_duplicates,
        "entities_created": entities_created,
        "relationships_created": relationships_created,
    }'''
assert s.count(old_return) == 1, "db.commit()/return anchor not found/not unique"
s = s.replace(old_return, new_return, 1)

open(p, "w").write(s)
print("aios_service.py patched")
