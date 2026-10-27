.PHONY: test ui engine desktop smoke server

test:            ## engine unit and integration tests
	cd engine && python -m unittest discover -s tests -t .

ui:              ## build the UI into ui/dist
	cd ui && node build.mjs

engine:          ## run the engine in development mode (desktop mode, dev key)
	cd engine && DESK_DEV=1 DESK_UI_DIR=../ui/dist DESK_LOCAL_TOKEN=$$(python -c "print('d'*40)") python -m desk_engine

desktop:         ## start the desktop app from source
	cd desktop && npm start

smoke: ui        ## browser smoke test with a fake model
	python e2e/ui_smoke.py

server:          ## start the Team Server (needs server/.env)
	cd server && docker compose up -d --build
