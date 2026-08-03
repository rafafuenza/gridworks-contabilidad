def test_la_base_de_pruebas_levanta(db):
    from app.models import Purchase

    assert db.query(Purchase).count() == 0
