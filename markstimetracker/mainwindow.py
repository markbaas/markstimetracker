import datetime
import json
import logging
import os
import os.path
import re
import sys
from distutils import dir_util

from dateutil import relativedelta

import dbus
from dbus.mainloop.pyqt5 import DBusQtMainLoop
from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QMainWindow, QSystemTrayIcon, QWidget
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .event_ui import Ui_Event
from .mainwindow_ui import Ui_MainWindow
from .models import Event, Task, init_db
from .redminesync import RedmineSync

sys.path.insert(0, os.path.dirname(__file__))


TAB_MAIN = 0
TAB_EDIT_EVENT = 1
TAB_SETTINGS = 2
TAB_EDIT_TASKS = 3


class MarksTimeTracker(QMainWindow, Ui_MainWindow):
    runningEvent = None

    def __init__(self, parent=None):
        super(MarksTimeTracker, self).__init__(parent)
        self.setupUi(self)
        self.tabWidget.tabBar().hide()
        self.setupStatusIcon()

        # config
        self.config_path = os.path.join(os.path.expanduser('~'), '.config', 'markstimetracker')
        dir_util.mkpath(self.config_path)
        self.readConfig()

        # Setup DB
        engine = create_engine('sqlite:///' + os.path.join(self.config_path, 'markstimetracker.db'))
        init_db(engine)
        self.db = sessionmaker(bind=engine)()

        self.updateTaskList()
        self.updateTasksComboBox()
        self.checkForRunningTask()

        # Timers
        timer = QTimer(self)
        timer.timeout.connect(self.updateTimeSpent)
        timer.start(1000)

        self.idleTimeTimer = QTimer()
        self.idleTimeTimer.timeout.connect(self.detectIdleTime)
        self.checkIdleTime()

        self.remindTimer = QTimer()
        self.remindTimer.timeout.connect(self.remindTracking)
        self.checkRemind()

        self.redmineSyncTimer = QTimer()
        self.redmineSyncTimer.timeout.connect(self.doRedmineSync)
        self.checkRedmineSync()

        # Events
        self.startButton.clicked.connect(self.toggleEventButton)
        self.eventsPeriodComboBox.currentIndexChanged.connect(self.eventsPeriodChanged)
        self.editDurationSpinBox.valueChanged.connect(self.updateEditStartEndTime)
        self.editStartDateTimeEdit.dateTimeChanged.connect(self.updateDurationSpinBoxEndTime)
        self.editEndDateTimeEdit.dateTimeChanged.connect(self.updateDurationSpinBox)
        self.editButtonBox.accepted.connect(self.saveEvent)
        self.editButtonBox.rejected.connect(lambda: self.tabWidget.setCurrentIndex(TAB_MAIN))
        self.settingsButtonBox.accepted.connect(self.saveSettings)
        self.settingsButtonBox.rejected.connect(lambda: self.tabWidget.setCurrentIndex(TAB_MAIN))
        self.settingsPushButton.clicked.connect(
            lambda: self.tabWidget.setCurrentIndex(TAB_SETTINGS))
        self.redmineSyncPushButton.clicked.connect(lambda: self.doRedmineSync(check=False))
        self.addEventPushButton.clicked.connect(self.addEvent)

        self.setupDbus()

    def setupStatusIcon(self):
        icon = QIcon()
        icon.addPixmap(QPixmap(":/clock.svg"), QIcon.Normal, QIcon.Off)
        self.statusIcon = QSystemTrayIcon(self)
        self.statusIcon.setIcon(icon)
        self.statusIcon.activated.connect(lambda: self.hide()
                                          if self.isVisible()
                                          else self.show())
        self.statusIcon.setToolTip("Mark's Time Tracker")
        self.statusIcon.show()

    def setupDbus(self):
        dbus_loop = DBusQtMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus(mainloop=dbus_loop)
        signals = [('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver', 'ActiveChanged'),
                   ('com.canonical.Unity', '/com/canonical/Unity/Session', 'Locked')]
        for org, path, event in signals:
            screensaver = self.bus.get_object(org, path)
            screensaver.connect_to_signal(event, self.checkLockScreen)

    def updateTasksComboBox(self):
        self.tasksComboBox.clear()
        self.editTaskListComboBox.clear()
        self.tasksComboBox.addItem('')
        self.tasksComboBox.lineEdit().setPlaceholderText("What are you going to do?")
        for task in self.db.query(Task).filter(Task.active == True):
            self.tasksComboBox.addItem(task.description)
            self.editTaskListComboBox.addItem(task.description)

    def updateTimeSpent(self):
        if self.runningEvent:
            spent_time = self.runningEvent.spent_time
            m, s = divmod(spent_time, 60)
            h, m = divmod(m, 60)
            self.timeLabel.setText("{h:02d}:{m:02d}:{s:02d}".format(h=h, m=m, s=s))

            period = self.eventsPeriodComboBox.currentText()
            start, end = self.getStartEndForPeriod(period)
            total = Event.get_spent_time_period(self.db, start, end)
            self.totalTimeLabel.setText("{}h".format(total))

    def getStartEndForPeriod(self, period):
        if period == "Today":
            start = datetime.datetime.now().replace(hour=0, minute=0)
            end = start + relativedelta.relativedelta(days=1)
        elif period == "Yesterday":
            end = datetime.datetime.now().replace(hour=0, minute=0)
            start = end - relativedelta.relativedelta(days=1)
        elif period == "This week":
            today = datetime.datetime.now().replace(hour=0, minute=0)
            start = today - relativedelta.relativedelta(days=today.weekday())
            end = today + relativedelta.relativedelta(days=6 - today.weekday())
        else:
            raise Exception("Don't know this period {}".format(period))

        return start, end

    def updateTaskList(self):
        while self.timeEntriesLayout.count() > 0:
            self.timeEntriesLayout.takeAt(0).widget().deleteLater()

        period = self.eventsPeriodComboBox.currentText()
        start, end = self.getStartEndForPeriod(period)

        events = self.db.query(Event).filter(Event.start.between(start, end))\
            .order_by(Event.start.desc())

        for event in events:
            if not event.end:
                continue
            widget = EventWidget(event.id, event.task.description, event.spent_time, parent=self)
            widget.clicked.connect(self.eventClicked)
            widget.show()
            self.timeEntriesLayout.addWidget(widget)

    def updateEditStartEndTime(self):
        hours = self.editDurationSpinBox.value()
        startTime = self.editStartDateTimeEdit.dateTime().toPyDateTime()
        newEndTime = startTime + relativedelta.relativedelta(hours=hours)
        self.editEndDateTimeEdit.setDateTime(newEndTime)

    def updateDurationSpinBox(self):
        seconds = float((self.editEndDateTimeEdit.dateTime().toPyDateTime() -
                         self.editStartDateTimeEdit.dateTime().toPyDateTime()).seconds)
        hours = seconds / 3600
        self.editDurationSpinBox.setValue(hours)

    def updateDurationSpinBoxEndTime(self):
        self.updateDurationSpinBox()
        self.updateEditStartEndTime()

    def checkForRunningTask(self):
        self.runningEvent = self.db.query(Event).filter(Event.end == None).first()
        if self.runningEvent:
            self.tasksComboBox.setCurrentIndex(
                [self.tasksComboBox.itemText(x) for x in range(self.tasksComboBox.count())]
                .index(self.runningEvent.task.description))
            self.startButton.setText("Stop")
            self.tasksComboBox.setEnabled(False)

    def toggleEventButton(self):
        if self.runningEvent:
            self.stopEvent()
        else:
            self.startEvent()

    def eventsPeriodChanged(self):
        self.updateTaskList()

    def eventClicked(self, event_id):
        event = self.db.query(Event).get(event_id)
        self.editTaskListComboBox.setCurrentIndex(
            [self.editTaskListComboBox.itemText(x)
             for x in range(self.editTaskListComboBox.count())]
            .index(event.task.description))

        self.editDurationSpinBox.setValue(float(event.spent_time) / 3600)
        self.editStartDateTimeEdit.setDateTime(event.start_date)
        self.editEndDateTimeEdit.setDateTime(event.end_date)

        self.tabWidget.setCurrentIndex(TAB_EDIT_EVENT)

        self.editingEvent = event

    def startEvent(self, event=None):
        if not event:
            event = self.tasksComboBox.currentText()

        self.tasksComboBox.setEnabled(False)

        self.startButton.setText("Stop")

        if not event:
            return

        if re.match(r'\d+ - .+', event):
            tracker_id, name = re.findall(r'(\d+) - (.+)', event)[0]
        else:
            tracker_id = None
            name = event

        # Update DB
        task = Task.get_or_create(self.db, task_id=tracker_id, name=name, parent=None)

        if self.runningEvent:
            self.runningEvent.end = datetime.datetime.now()

        self.runningEvent = Event(task_id=task.task_id, comment="", start=datetime.datetime.now())
        self.db.add(self.runningEvent)
        self.db.commit()

        self.tasksComboBox.lineEdit().setText(self.runningEvent.task.description)

        self.checkForRunningTask()

    def addEvent(self):
        self.editDurationSpinBox.setValue(1)
        self.editStartDateTimeEdit.setDateTime(datetime.datetime.now())
        self.editEndDateTimeEdit.setDateTime(datetime.datetime.now() +
                                             relativedelta.relativedelta(hours=1))

        self.tabWidget.setCurrentIndex(TAB_EDIT_EVENT)
        self.editingEvent = Event()
        self.db.add(self.editingEvent)

    def stopEvent(self):
        self.tasksComboBox.setEnabled(True)

        self.runningEvent.end = datetime.datetime.now()
        self.db.commit()

        self.runningEvent = None

        self.updateTaskList()

        self.startButton.setText("Start")
        self.timeLabel.setText("00:00:00")

        self.updateTasksComboBox()

    def saveEvent(self):

        self.editingEvent.task_id = self.editTaskListComboBox.currentText().split(' - ')[0]
        self.editingEvent.start = self.editStartDateTimeEdit.dateTime().toPyDateTime()
        self.editingEvent.end = self.editEndDateTimeEdit.dateTime().toPyDateTime()

        self.db.commit()

        self.tabWidget.setCurrentIndex(TAB_MAIN)
        self.updateTaskList()

    def saveSettings(self):
        self.config = {'enable_detect_idle_time': self.detectIdleTimecheckBox.checkState(),
                       'detect_idle_time': self.detectIdleTimeSpinBox.value(),
                       'enable_remind': self.remindCheckBox.checkState(),
                       'remind_time': self.remindSpinBox.value(),
                       'stop_on_lock_screen': self.stopLockScreencheckBox.checkState(),
                       'enabled_redmine_sync': self.syncRedmineCheckBox.checkState(),
                       'redmine_sync_time': self.redmineSyncTimeSpinBox.value(),
                       'redmine_apikey': self.redmineApikeyLineEdit.text(),
                       'redmine_url': self.redmineUrlLineEdit.text(),
                       'redmine_user': self.redmineUserLineEdit.text()}
        self.writeConfig()

    def readConfig(self):
        config_file_path = os.path.join(self.config_path, 'config.json')
        if os.path.exists(config_file_path):
            with open(config_file_path, 'r') as f:
                self.config = json.loads(f.read())
        else:
            self.config = {}

        self.detectIdleTimecheckBox.setCheckState(self.config.get('enable_detect_idle_time', True))
        self.detectIdleTimeSpinBox.setValue(self.config.get('detect_idle_time'))
        self.remindCheckBox.setCheckState(self.config.get('enable_remind', True))
        self.remindSpinBox.setValue(self.config.get('remind_time'))
        self.stopLockScreencheckBox.setCheckState(self.config.get('stop_on_lock_screen', True))
        self.syncRedmineCheckBox.setCheckState(self.config.get('enabled_redmine_sync'))
        self.redmineSyncTimeSpinBox.setValue(self.config.get('redmine_sync_time'))
        self.redmineApikeyLineEdit.setText(self.config.get('redmine_apikey'))
        self.redmineUrlLineEdit.setText(self.config.get('redmine_url'))
        self.redmineUserLineEdit.setText(self.config.get('redmine_user'))

    def writeConfig(self):
        with open(os.path.join(self.config_path, 'config.json'), 'w') as f:
            f.write(json.dumps(self.config))
        self.tabWidget.setCurrentIndex(TAB_MAIN)

        self.checkIdleTime()
        self.checkRemind()
        self.checkRedmineSync()

    def checkIdleTime(self):
        self.idleTimeTimer.stop()
        if self.config.get("enable_detect_idle_time", True):
            self.idleTimeTimer.start(self.config.get("detect_idle_time", 5) * 60000)

    def detectIdleTime(self):

        # do something

        self.checkIdleTime()

    def checkRemind(self):
        self.remindTimer.stop()
        if self.config.get("enable_remind", True):
            self.remindTimer.start(self.config.get("remind_time", 5) * 60000)

    def remindTracking(self):

        # do something

        self.checkRemind()

    def checkRedmineSync(self):
        self.redmineSyncTimer.stop()
        if self.config.get("enabled_redmine_sync"):
            self.redmineSyncTimer.start(self.config.get("redmine_sync_time", 5) * 60000)

        self.redmineSyncPushButton.setVisible(self.config.get("enabled_redmine_sync", False))

    def doRedmineSync(self, check=True):
        logging.info("Doing redmine sync")
        thread = RedmineSyncThread(self.config,
                                   'sqlite:///' +
                                   os.path.join(self.config_path, 'markstimetracker.db'))

        def updateTaskWidgets():
            self.updateTaskList()
            self.updateTasksComboBox()

        thread.finished.connect(updateTaskWidgets)
        thread.start()

        self.checkRedmineSync()

    def checkLockScreen(self, is_locked=True):
        if is_locked and self.config.get("stop_on_lock_screen"):
            self.stopEvent()


class EventWidget(QWidget, Ui_Event):

    clicked = pyqtSignal([int])

    def __init__(self, event_id, event_name, duration, parent=None):
        super(EventWidget, self).__init__(parent)
        self.setupUi(self)

        self.event_id = event_id

        self.taskLabel.setText(event_name)
        m, s = divmod(duration, 60)
        h, m = divmod(m, 60)
        self.timeLabel.setText("{h:02d}:{m:02d}:{s:02d}".format(h=h, m=m, s=s))

        self.startButton.clicked.connect(lambda x: parent.startEvent(event=self.taskLabel.text()))

    def mouseReleaseEvent(self, ev):
        self.clicked.emit(self.event_id)


class RedmineSyncThread(QThread):

    def __init__(self, config, db, parent=None):
        QThread.__init__(self, parent)
        self.config = config
        self.db = db

    def run(self):
        redminesync = RedmineSync(self.config, sessionmaker(bind=create_engine(self.db))())
        redminesync.sync_redmine_issues()
        redminesync.sync_timeentries(period='last_and_current_week')

    def __del__(self):
        self.wait()
