from fastapi import FastAPI

app = FastAPI(title="ContextOS Backend")

@app.get("/")
def read_root():
    return {"status": "ContextOS backend is running"}
