from pydantic import BaseModel, Field, SecretStr


class GitHubConnectRequest(BaseModel):
    access_token: SecretStr
    repository: str | None = Field(default=None, max_length=255)
    account: str | None = Field(default=None, max_length=255)


class IntegrationResponse(BaseModel):
    id: str
    provider: str
    config: dict
