"""Embedded Jobs Manager for day-to-day Matterport operations."""

import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from app.security.user_manager import AuthorizationError
from app.services.jobs_service import JobsService
from app.ui.job_form import changed_fields, show_job_form
from app.ui.styles import PADDING


EXPECTED