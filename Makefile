PYTHON ?= python
PYTESTS ?= pytest
CODESPELL_SKIPS ?= "doc/auto_*,*.fif,*.eve,*.gz,*.tgz,*.zip,*.mat,*.stc,*.label,*.w,*.bz2,*.annot,*.sulc,*.log,*.local-copy,*.orig_avg,*.inflated_avg,*.gii,*.pyc,*.doctree,*.pickle,*.inv,*.png,*.edf,*.touch,*.thickness,*.nofix,*.volume,*.defect_borders,*.mgh,lh.*,rh.*,COR-*,FreeSurferColorLUT.txt,*.examples,.xdebug_mris_calc,bad.segments,BadChannels,*.hist,empty_file,*.orig,*.js,*.map,*.ipynb,searchindex.dat,install_mne_c.rst,plot_*.rst,*.rst.txt,c_EULA.rst*,*.html,gdf_encodes.txt,*.svg"
CODESPELL_DIRS ?= seek_localize/ doc/ examples/

all: clean inplace test

# variables
name := seek_localize
version := 1.0.0
dockerhub := neuroseek

############################## UTILITY FOR PYTHON #########################
clean-pyc:
	find . -name "*.pyc" | xargs rm -f
	find . -name "*.DS_Store" | xargs rm -f

#clean-so:
#	find . -name "*.so" | xargs rm -f
#	find . -name "*.pyd" | xargs rm -f

clean-build:
	rm -rf _build
	rm -rf dist
	rm -rf seek_localize.egg-info

clean-ctags:
	rm -f tags

clean-test:
	rm -rf .mypy_cache
	rm -rf .pytest_cache
	rm junit-results.xml

#clean-cache:
	#find . -name "__pychache__" | xargs rm -rf

clean: clean-build clean-pyc clean-ctags clean-test

reqs:
	pipfile2req --dev > test_requirements.txt
	pipfile2req > requirements.txt
	pipfile2req > docs/requirements.txt
	pipfile2req --dev >> docs/requirements.txt

codespell:  # running manually
	@codespell -w -i 3 -q 3 -S $(CODESPELL_SKIPS) --ignore-words=ignore_words.txt $(CODESPELL_DIRS)

codespell-error:  # running on travis
	@echo "Running code-spell check"
	@codespell -i 0 -q 7 -S $(CODESPELL_SKIPS) --ignore-words=ignore_words.txt $(CODESPELL_DIRS)

inplace:
	$(PYTHON) setup.py install

test: inplace check-manifest
	rm -f .coverage
	$(PYTESTS) ./
	cd seek_localize/pipeline/01-prep/

test-doc:
	$(PYTESTS) --doctest-modules --doctest-ignore-import-errors

build-doc:
	cd docs; make clean
	cd docs; make html

#upload-pipy:
#	python setup.py sdist bdist_egg register upload

build-pipy:
	python setup.py sdist bdist_wheel

test-pipy:
	twine check dist/*
	twine upload --repository testpypi dist/*

upload-pipy:
	twine upload dist/*

pydocstyle:
	@echo "Running pydocstyle"
	@pydocstyle

pycodestyle:
	@echo "Running pycodestyle"
	@pycodestyle

check-manifest:
	check-manifest --ignore .circleci*,docs,.DS_Store,annonymize

black:
	@if command -v black > /dev/null; then \
		echo "Running black"; \
		black --check seek_localize/; \
		black seek_localize/; \
		black examples/; \
	else \
		echo "black not found, please install it!"; \
		exit 1; \
	fi;
	@echo "black passed"


type-check:
	mypy ./seek_localize

check:
	@$(MAKE) -k black pydocstyle codespell-error check-manifest type-check
