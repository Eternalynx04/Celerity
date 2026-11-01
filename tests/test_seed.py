from sqlalchemy import func, select

from celerity.database import make_sessions
from celerity.models import Order, OrderItem
from celerity.seed import seed_demo


def test_seed_is_repeatable_and_does_not_duplicate_records(engine):
    assert seed_demo(engine, products=30, customers=10, orders=100) == {
        "products": 30,
        "customers": 10,
        "orders": 100,
    }
    with make_sessions(engine)() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 100
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 100
