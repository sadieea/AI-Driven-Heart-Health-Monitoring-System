import psycopg2

DB_CONFIG = {
    "dbname": "ecg_heart_health_monitoring_db",
    "user": "postgres",
    "password": "S@diee11",
    "host": "localhost",
    "port": 5432
}

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

with open('setup_database.sql', 'r') as f:
    sql = f.read()

try:
    cur.execute(sql)
    conn.commit()
    print("Database setup completed successfully.")
except Exception as e:
    conn.rollback()
    print(f"Error: {e}")
finally:
    cur.close()
    conn.close()