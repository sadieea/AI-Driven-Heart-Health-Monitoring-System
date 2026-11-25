import psycopg2

# Connect to the default postgres database to create the new one
conn = psycopg2.connect(
    dbname="postgres",
    user="postgres",
    password="S@diee11",
    host="localhost",
    port=5432
)

conn.autocommit = True
cur = conn.cursor()

try:
    cur.execute("CREATE DATABASE ecg_heart_health_monitoring_db;")
    print("Database created successfully.")
except psycopg2.errors.DuplicateDatabase:
    print("Database already exists.")
finally:
    cur.close()
    conn.close()