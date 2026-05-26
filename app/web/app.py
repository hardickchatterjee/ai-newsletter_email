from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.web.routes import auth, dashboard


def create_app() -> FastAPI:
    app = FastAPI(title="AI News Aggregator")

    app.include_router(auth.router)
    app.include_router(dashboard.router)

    @app.get("/")
    async def root():
        return RedirectResponse(url="/dashboard")

    return app


app = create_app()
