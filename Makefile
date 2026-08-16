.PHONY: install dev test migrate upgrade seed check

install:
	pip install -r requirements-dev.txt

dev:
	APP_ENV=development FLASK_APP=wsgi.py flask run --debug --port 5000

test:
	APP_ENV=testing python -m pytest -q

migrate:
	APP_ENV=development FLASK_APP=wsgi.py flask db migrate -m "$(m)"

upgrade:
	APP_ENV=development FLASK_APP=wsgi.py flask db upgrade

seed:
	APP_ENV=development FLASK_APP=wsgi.py flask seed-church \
		--slug journey --name "The Journey Church" \
		--city Jackson --state MO --timezone America/Chicago \
		--accent "#2563FF" \
		--host app.thejourneychurchsemo.com \
		--host journey.dos.betweensundaysconsulting.com

check:
	APP_ENV=development FLASK_APP=wsgi.py flask check-boot
