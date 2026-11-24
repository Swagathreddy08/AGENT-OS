class NonRetryableWorkflowError(ValueError):
    """A workflow definition or node configuration cannot succeed by retrying."""


class ApprovalRequired(Exception):
    def __init__(self, payload: dict, node_id: str):
        self.payload = payload
        self.node_id = node_id
