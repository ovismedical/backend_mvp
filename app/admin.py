import re

from fastapi import APIRouter, Depends, HTTPException
from .login import get_db, get_user

adminrouter = APIRouter(prefix="/admin", tags=["admin"])

RELATED_COLLECTIONS = [
    "answers",
    "florence_assessments",
    "calendar_credentials",
    "symptom_questionnaires",
    "questionnaire_drafts",
    "user_achievements",
]


def require_doctor(user=Depends(get_user)):
    if not user.get("isDoctor"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@adminrouter.get("/users")
def list_users(search: str = None, user=Depends(require_doctor), db=Depends(get_db)):
    query = {}
    if search:
        escaped = re.escape(search)
        query = {"$or": [
            {"username": {"$regex": escaped, "$options": "i"}},
            {"email": {"$regex": escaped, "$options": "i"}},
            {"full_name": {"$regex": escaped, "$options": "i"}},
        ]}

    users = list(db["users"].find(query, {"password": 0}))
    for u in users:
        u["_id"] = str(u["_id"])
    return {"users": users, "count": len(users)}


@adminrouter.get("/doctors")
def list_doctors(user=Depends(require_doctor), db=Depends(get_db)):
    doctors = list(db["doctors"].find({}, {"password": 0}))
    for d in doctors:
        d["_id"] = str(d["_id"])
    return {"doctors": doctors, "count": len(doctors)}


@adminrouter.delete("/users/{username}")
def delete_user(username: str, user=Depends(require_doctor), db=Depends(get_db)):
    existing = db["users"].find_one({"username": username})
    if not existing:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")

    result = db["users"].delete_one({"username": username})
    cleaned = {"users": result.deleted_count}

    for coll_name in RELATED_COLLECTIONS:
        r = db[coll_name].delete_many({"user_id": username})
        if r.deleted_count > 0:
            cleaned[coll_name] = r.deleted_count

    return {"deleted": username, "cleaned": cleaned}


@adminrouter.get("/stats")
def db_stats(user=Depends(require_doctor), db=Depends(get_db)):
    stats = {}
    for name in db.list_collection_names():
        stats[name] = db[name].count_documents({})
    return {"database": db.name, "collections": stats}
