export PGHOST=localhost PGPORT=5432
export PGDATABASE=$(kubectl get secret -n default postgres-secret -o jsonpath='{.data.POSTGRES_DB}' | base64 -d)
export PGUSER=$(kubectl get secret -n default postgres-secret -o jsonpath='{.data.POSTGRES_USER}' | base64 -d)
export PGPASSWORD=$(kubectl get secret -n default postgres-secret -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
psql

