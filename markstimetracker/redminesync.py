#!/usr/bin/env python3

import logging
import os
import os.path
import sys

from redmine import Redmine
from redmine.exceptions import ResourceAttrError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Task


class RedmineSync:
    redm = None
    session = None
    period = None
    redmine_user_id = None
    redmine_apikey = None
    redmine_url = None

    def __init__(self, config, db):
        # Do checks
        if not config.get('redmine_apikey') or not config.get('redmine_user') \
                or not config.get('redmine_url'):
            return

        self.redmine_user_id = config.get('redmine_user')
        self.redmine_apikey = config.get('redmine_apikey')
        self.redmine_url = config.get('redmine_url')
        self.session = db

        self.redm = Redmine(self.redmine_url, key=self.redmine_apikey)

    def _create_db_session(self):
        if not os.path.exists(self.db_path):
            logging.error('Database path is incorrect')
            sys.exit()

        engine = create_engine('sqlite:///' + self.db_path)
        self.session = sessionmaker(bind=engine)()

    def _get_tasks(self):
        return self.session.query(Task)

    def _push_time_entry(self, spent_time, issue_id, date):
        entries = self.redm.time_entry.filter(spent_on=date, issue_id=issue_id,
                                              user_id=self.redmine_user_id)

        if len(entries) > 1:
            logging.warning('Sync does not support update multiple entries.')

        data = dict(spent_on=date, issue_id=issue_id, hours=spent_time, activity_id=9)
        if len(entries) == 1:
            if entries[0].hours != spent_time:
                self.redm.time_entry.update(entries[0].id, **data)
                logging.warning('  {}: {} hours (updated)'.format(date, spent_time))
        else:
            self.redm.time_entry.create(**data)
            logging.warning('  {}: {} hours (new)'.format(date, spent_time))

    def sync_timeentries(self, period='week'):
        for task in self._get_tasks():
            time_entries = task.get_spent_time_per_day(period=period).items()
            if not time_entries:
                continue

            # logging.warning('#{} {}'.format(task.task_id, task.name))

            for date, spent_time in time_entries:
                if spent_time:
                    self._push_time_entry(spent_time, task.task_id, date)

    def sync_redmine_issues(self):
        issues = self.redm.issue.filter(assigned_to_id='me')

        for issue in issues:
            task = self.session.query(Task)\
                .filter(Task.task_id == issue.id).first()
            if not task:
                try:
                    parent_id = issue.parent.id
                except ResourceAttrError:
                    parent_id = 0

                obj = Task(name=issue.subject, task_id=issue.id, parent=parent_id, active=True, redmine=True)
                self.session.add(obj)
                logging.warning('Added task {}'.format(issue.subject))
            elif issue.subject != task.name:
                task.name = issue.subject
                self.session.add(task)

        # Check consistency
        issue_ids = [i.id for i in issues]
        for task in self.session.query(Task).filter(Task.redmine is True):
            if task.parent:
                parent = self.session.query(Task).filter(Task.task_id == task.parent).first()
                if not parent:
                    task.parent = 0
                    self.session.add(task)
            if task.task_id not in issue_ids:
                task.active = 0
                self.session.add(task)

        self.session.commit()
