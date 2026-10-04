from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple

class BaseInspectionRepository(ABC):
    """
    Abstract repository interface for inspection reports.
    Decouples business logic from the storage implementation (in-memory for local
    development, MongoDB locally, or Amazon DocumentDB in production).
    Enforces user ownership scoping across all retrieval, mutation, and query operations.
    """

    @abstractmethod
    async def get_all(
        self,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        sort_by: str = "inspection_date",
        sort_order: str = "desc",
        page: int = 1,
        limit: int = 10
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Fetch paginated reports with search and filter parameters scoped to authenticated user."""
        pass

    @abstractmethod
    async def get_by_id(self, report_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch a single inspection report by ID scoped to authenticated user."""
        pass

    @abstractmethod
    async def create(self, report_data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        """Create and store a new inspection report owned by authenticated user."""
        pass

    @abstractmethod
    async def update(self, report_id: str, update_data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update an existing inspection report only if owned by authenticated user."""
        pass

    @abstractmethod
    async def delete(self, report_id: str, user_id: Optional[str] = None) -> bool:
        """Delete an inspection report by ID only if owned by authenticated user."""
        pass

    @abstractmethod
    async def query_nested(
        self,
        conditions: List[Dict[str, Any]],
        match_type: str = "and",
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], float]:
        """
        Execute arbitrary nested queries using dot notation syntax matching
        MongoDB/Amazon DocumentDB query semantics, scoped to authenticated user.
        """
        pass

    @abstractmethod
    async def query_raw(
        self,
        raw_filter: Dict[str, Any],
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], float]:
        """Execute raw MongoDB-compatible query scoped to authenticated user."""
        pass

    @abstractmethod
    async def get_schema_overview(self, user_id: Optional[str] = None) -> Any:
        """Discover variable schemas scoped to authenticated user."""
        pass

    @abstractmethod
    async def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Compute aggregated statistics across reports owned by authenticated user."""
        pass
