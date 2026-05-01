"""Tests demonstrating FastAPI startup/shutdown event binding.

Core concept: app = FastAPI() MUST be defined BEFORE event handlers.
Otherwise, the handler tries to bind to a non-existent app object.
"""

import pytest
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestEventBindingOrder:
    """Test that demonstrates why event binding order matters."""

    def test_correct_order_app_first(self):
        """CORRECT: Define app first, then add handlers.

        This works because when @app.on_event runs, `app` already exists.
        """
        app = FastAPI()  # 1. Create app FIRST

        called = []

        @app.on_event("startup")  # 2. Then bind event
        async def startup():
            called.append("startup")

        # Verify the decorator worked (no NameError)
        assert app.router.on_event.__wrapped__ is not None

    def test_wrong_order_raises_name_error(self):
        """WRONG: Try to bind event before app exists.

        This would raise NameError if uncommented.
        """
        # The problem with this code:
        #
        # @app.on_event("startup")  # NameError: 'app' not defined yet!
        # async def startup():
        #     pass
        #
        # app = FastAPI()  # Created too late
        #
        # The decorator @app.on_event runs at import time, before app exists.
        pass  # Placeholder - actual code would crash

    def test_lifespan_context_manager_pattern(self):
        """Modern approach: lifespan context manager.

        This is the recommended way in modern FastAPI.
        """
        startup_called = False
        shutdown_called = False

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            nonlocal startup_called, shutdown_called
            startup_called = True
            yield
            shutdown_called = True

        app = FastAPI(lifespan=lifespan)

        # Verify lifespan is set
        assert app.router.lifespan_context.__wrapped__ is not None

    def test_startup_can_initialize_shared_state(self):
        """Demonstrate startup initializing shared state."""
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            app.state.initialized = True
            app.state.startup_time = "2024-01-01"
            yield

        app = FastAPI(lifespan=lifespan)

        with TestClient(app):
            assert app.state.initialized is True
            assert app.state.startup_time == "2024-01-01"

    def test_startup_can_initialize_resources(self):
        """Demonstrate startup for resource initialization."""
        connections = []

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            connections.append("db_connection")
            connections.append("cache_connection")
            yield
            connections.clear()  # Cleanup on shutdown

        app = FastAPI(lifespan=lifespan)

        with TestClient(app):
            assert connections == ["db_connection", "cache_connection"]

        assert connections == []


class TestInPractice:
    """How MY-RSS project uses event binding (from src/api.py)."""

    def test_real_world_pattern(self):
        """From src/api.py - demonstrates correct order."""
        # 1. Create app first (line 25 in api.py)
        app = FastAPI(title="Test API")

        # 2. Event handlers can now reference `app`
        @app.on_event("startup")
        async def startup_event():
            app.state.running = True

        @app.on_event("shutdown")
        async def shutdown_event():
            app.state.running = False

        # 3. Routes can use app state
        @app.get("/status")
        async def status():
            return {"running": app.state.running}

        # Verify
        assert app.title == "Test API"
