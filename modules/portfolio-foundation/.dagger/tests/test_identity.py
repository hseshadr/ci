import pytest

from portfolio_foundation.identity import FullSha, RepositoryRef


def test_should_accept_lowercase_full_sha() -> None:
    # Given
    value = "a" * 40

    # When
    sha = FullSha(value)

    # Then
    assert sha.value == value


def test_should_parse_canonical_repository_when_owner_and_name_are_present() -> None:
    # Given
    value = "owner/repository"

    # When
    repository = RepositoryRef.parse(value)

    # Then
    assert repository.github_url == "https://github.com/owner/repository.git"


@pytest.mark.parametrize("value", ("abc1234", "A" * 40, "g" * 40, ""))
def test_should_reject_noncanonical_sha_when_value_is_invalid(value: str) -> None:
    # Given
    invalid_sha = value

    # When / Then
    with pytest.raises(ValueError, match="lowercase 40-character"):
        FullSha(invalid_sha)


@pytest.mark.parametrize("value", ("edge-reco", "owner/repo/extra", "owner repo"))
def test_should_reject_invalid_repository_when_owner_and_name_are_missing(value: str) -> None:
    # Given
    invalid_repository = value

    # When / Then
    with pytest.raises(ValueError, match="owner/repository"):
        RepositoryRef.parse(invalid_repository)
