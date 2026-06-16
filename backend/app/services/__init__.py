"""Service layer.

Services hold the easy-to-get-wrong business logic (cycle prevention, delete
guards, constraint enforcement, default-value resolution).  Routes call services
rather than repositories directly for any operation that involves business rules.

Repositories remain pure data-access objects.
"""
