"""
Shared route dependencies.

`load_owned_contract` is the single place the ownership rule is expressed. Every route that
reads a contract goes through it, so there is one function to audit rather than six
hand-written `select()` calls that must each remember the owner filter.

A contract belonging to someone else returns **404, not 403**: a 403 confirms the UUID
exists, which is exactly the information an enumeration attack is after.
"""
import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contract
from app.db.session import get_db
from app.middleware import current_owner

logger = logging.getLogger(__name__)

NOT_FOUND_DETAIL = "Contract not found"


async def load_owned_contract(
    contract_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Contract:
    """
    Fetch a contract the caller owns, or raise 404.

    A malformed UUID also yields 404 rather than a 500 from asyncpg: the id comes straight
    from the URL, so it is untrusted input.
    """
    owner_id = current_owner(request)
    try:
        result = await db.execute(
            select(Contract).where(Contract.id == contract_id, Contract.owner_id == owner_id)
        )
    except Exception as e:
        logger.info(f"Rejected contract lookup for {contract_id!r}: {e}")
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from e

    contract = result.scalar_one_or_none()
    if contract is None:
        # Deliberately indistinguishable from "does not exist" — see the module docstring.
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
    return contract
