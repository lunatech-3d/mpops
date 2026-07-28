"""Markets lookup-table manager."""

from app.services.market_service import MarketService
from app.ui.lookup_manager import LookupManager, LookupManagerConfig


class MarketManager(LookupManager):
    """Configure the reusable lookup manager for the Markets table."""

    def __init__(self, parent, auth, session):
        service = MarketService(auth)
        super().__init__(
            parent,
            LookupManagerConfig(
                singular_name="Market",
                plural_name="Markets",
                id_field="market_id",
                name_field="market_name",
                list_records=service.list_markets,
                search_records=service.search_markets,
                get_record=service.get_market,
                create_record=lambda name: service.create_market(session, name),
                update_record=lambda market_id, name: service.update_market(
                    session, market_id, name
                ),
                set_active=lambda market_id, active: service.set_market_active(
                    session, market_id, active
                ),
                can_modify=session.role == "admin",
            ),
        )
