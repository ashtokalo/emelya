.PHONY: test run

test:
	python3 -m unittest discover tests/ -v

run:
	./run.sh
