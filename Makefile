.PHONY: zip test clean

zip:
	python3 scripts/build_plugin.py

test:
	python3 -m unittest discover -s tests -v

clean:
	rm -rf dist build
