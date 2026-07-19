"""REST v1 router aggregation."""

from fastapi import APIRouter

from tiqora.api.v1 import auth, customers, queues, search, tickets

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(queues.router)
api_v1_router.include_router(tickets.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(search.router)

__all__ = ["api_v1_router"]
