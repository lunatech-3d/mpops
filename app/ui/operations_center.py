"""Matterport Ops Operations Center dispatch workspace."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError
from app.services.jobs_service import JobsService
from app.ui.job_form import changed_fields, show_job_form
from app.ui.styles import PADDING


EXPECTED