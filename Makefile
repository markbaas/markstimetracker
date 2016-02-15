build:
	pyuic5 --from-imports data/mainwindow.ui -o markstimetracker/mainwindow_ui.py
	pyuic5 --from-imports data/event.ui -o markstimetracker/event_ui.py
	pyrcc5 data/resources.qrc -o markstimetracker/resources_rc.py
	python3 setup.py build

install: build
	python3 setup.py install

.PHONY: build install
