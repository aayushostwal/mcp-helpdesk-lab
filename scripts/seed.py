"""Seed a database. Equivalent to `helpdesk seed`; kept as a script for convenience."""

from helpdesk.cli import app

if __name__ == "__main__":
    app(["seed", *__import__("sys").argv[1:]])
