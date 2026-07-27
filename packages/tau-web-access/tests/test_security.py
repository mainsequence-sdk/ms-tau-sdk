import pytest

from tau_web_access.security import UnsafeUrlError, validate_public_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/private",
    ],
)
async def test_private_network_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        await validate_public_url(url, allow_private_networks=False)
