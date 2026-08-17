# coding=utf-8
from services.inspection.views import inspection_bp


def test_inspection_blueprint_does_not_expose_raw_sql_execution():
    routes = {
        (route.uri, method)
        for route in inspection_bp._future_routes
        for method in route.methods
    }

    assert ("/sql/execute", "POST") not in routes
