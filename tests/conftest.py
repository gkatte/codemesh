"""Shared test fixtures."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# Suppress macOS MallocStackLogging noise from child processes.
# This env var gets set by Xcode/Instruments debugging sessions and
# causes harmless but noisy warnings when pytest forks subprocesses.
os.environ.pop("MallocStackLogging", None)
os.environ.pop("MALLOC_STACK_LOGGING", None)

from codemesh.db.connection import get_connection
from codemesh.db.schema import init_db


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def db_path(tmp_dir: Path) -> Path:
    path = tmp_dir / "test.db"
    init_db(path)
    return path


@pytest.fixture
def conn(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    with get_connection(db_path) as c:
        yield c


@pytest.fixture
def python_project(tmp_dir: Path) -> Path:
    (tmp_dir / "models.py").write_text('''
class User:
    """A user model."""
    def __init__(self, name: str, email: str) -> None:
        self.name = name
        self.email = email
    def validate(self) -> bool:
        """Validate user data."""
        return "@" in self.email and len(self.name) > 0

class Admin(User):
    """Admin user."""
    def __init__(self, name: str, email: str, role: str) -> None:
        super().__init__(name, email)
        self.role = role
    def validate(self) -> bool:
        return super().validate() and self.role in ("admin", "superadmin")
''')
    (tmp_dir / "services.py").write_text('''
from models import User, Admin

def create_user(name: str, email: str) -> User:
    """Create a new user."""
    user = User(name, email)
    if not user.validate():
        raise ValueError("Invalid user data")
    return user

def create_admin(name: str, email: str, role: str) -> Admin:
    """Create a new admin."""
    admin = Admin(name, email, role)
    if not admin.validate():
        raise ValueError("Invalid admin data")
    return admin
''')
    return tmp_dir


@pytest.fixture
def typescript_project(tmp_dir: Path) -> Path:
    (tmp_dir / "types.ts").write_text("""
export interface User { name: string; email: string; }
export interface Admin extends User { role: string; }
""")
    (tmp_dir / "services.ts").write_text("""
import { User } from "./types";
export function createUser(name: string, email: string): User { return { name, email }; }
export class UserService {
    private users: User[] = [];
    addUser(user: User): void { this.users.push(user); }
}
""")
    return tmp_dir


@pytest.fixture
def rust_project(tmp_dir: Path) -> Path:
    (tmp_dir / "models.rs").write_text("""
pub struct User { pub name: String, pub email: String }
impl User {
    pub fn new(name: &str, email: &str) -> Self { Self { name: name.to_string(), email: email.to_string() } }
    pub fn validate(&self) -> bool { self.email.contains("@") && !self.name.is_empty() }
}
""")
    return tmp_dir
