"""Database access layer.

Sadrži:
- ``engine``           — kreiranje SQLAlchemy async engine-a.
- ``schema_inspector`` — dinamički dohvat sheme baze (tablice, kolone, FK).

Sve interakcije s bazom prolaze kroz ovaj paket — pravilo "no hardcoded
schema" se ovdje materijalizira: drugi moduli pitaju SchemaInspector, ne
pretpostavljaju strukturu.
"""
