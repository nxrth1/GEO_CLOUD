from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from processes import -------

app = FastAPI(
    title="GEOSPATIAL_PROCESSES",
    description="Geospatial processing with FastAPI",
    version="1.0.0"
)

origins = [

    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
)
""" 
app.include_router(router)
Base.metadata.create_all(bind=engine)

app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "frontend", html=True),
    name="frontend",
)

"""