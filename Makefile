.PHONY: install test lint collectstatic runserver

install:
	python -m pip install --upgrade pip
	pip install -r requirements.txt

lint:
	python -m pip install ruff==0.15.13
	ruff check .

test:
	python manage.py test

collectstatic:
	python manage.py collectstatic --noinput

runserver:
	python manage.py runserver
