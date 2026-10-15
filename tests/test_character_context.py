"""
Unit tests for Character Context & Pronoun Consistency Module (Dramatis Personae).
"""

from unittest.mock import Mock, AsyncMock, MagicMock, patch
from pathlib import Path
import pytest

from app.models.runtime_config import RuntimeConfig
from app.clients.plex_client import PlexClient
from app.clients.openai_translation_client import OpenAITranslationClient
from app.services.subtitle_service import SubtitleService


@pytest.fixture
def mock_runtime_config():
    return RuntimeConfig(
        plex_url="http://localhost:32400",
        plex_token="test-token",
        openai_api_key="sk-test-key",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini",
    )


class TestPlexCharacterContext:
    """Test character metadata extraction from Plex."""

    def test_movie_characters_context(self, mock_runtime_config):
        client = PlexClient(mock_runtime_config, mock_mode=True)
        ctx = client.get_media_characters_context("1001")
        assert ctx["title"] == "Mock Media"
        assert len(ctx["roles"]) == 2
        assert ctx["roles"][0]["character"] == "Character One"
        assert ctx["parent_rating_key"] == "1001"

    @patch("app.clients.plex_client.PlexServer")
    def test_episode_characters_inherits_show_roles(self, mock_plex_cls, mock_runtime_config):
        mock_server = Mock()
        mock_plex_cls.return_value = mock_server
        client = PlexClient(mock_runtime_config, mock_mode=False)

        # Mock episode without direct roles
        mock_episode = Mock()
        mock_episode.type = "episode"
        mock_episode.title = "Episode 1"
        mock_episode.grandparentTitle = "My Show"
        mock_episode.grandparentRatingKey = 999
        mock_episode.year = 2024
        mock_episode.summary = "Episode summary"
        mock_episode.genres = []
        mock_episode.roles = []

        # Mock parent show with cast roles
        mock_show = Mock()
        mock_show.type = "show"
        role1 = Mock()
        role1.tag = "Actor A"
        role1.role = "Agatha"
        role2 = Mock()
        role2.tag = "Actor B"
        role2.role = "Rio"
        mock_show.roles = [role1, role2]

        client.server.fetchItem.side_effect = lambda rk: mock_episode if rk == 123 else mock_show

        ctx = client.get_media_characters_context("123")
        assert ctx["title"] == "Episode 1"
        assert ctx["show_title"] == "My Show"
        assert ctx["parent_rating_key"] == "999"
        assert len(ctx["roles"]) == 2
        assert ctx["roles"][0]["character"] == "Agatha"
        assert ctx["roles"][1]["character"] == "Rio"


class TestOpenAICharacterMatrix:
    """Test Character Matrix generation and prompt formatting."""

    def test_format_character_context_for_prompt(self, mock_runtime_config):
        ai = OpenAITranslationClient(mock_runtime_config)
        matrix = {
            "characters": [
                {"name": "Agatha", "gender": "female", "role": "witch"},
                {"name": "Rio", "gender": "female", "role": "rival"},
            ],
            "pronoun_rules": [
                {"pair": ["Agatha", "Rio"], "relationship": "rivals", "call": "tôi - cô / ả"},
            ],
            "guidelines": "Keep arrogant tone.",
        }
        formatted = ai.format_character_context_for_prompt(matrix, pronoun_override="Agatha - Rio: chị - em")
        assert "[QUY TẮC NHÂN VẬT & ĐẠI TỪ XƯNG HÔ TIẾNG VIỆT]" in formatted
        assert "Agatha (female): witch" in formatted
        assert "Agatha và Rio (rivals): xưng hô 'tôi - cô / ả'" in formatted
        assert "GHI ĐÈ ƯU TIÊN TỪ NGƯỜI DÙNG: Agatha - Rio: chị - em" in formatted
        assert "BẮT BUỘC: Tuân thủ đúng giới tính" in formatted

    @pytest.mark.asyncio
    async def test_generate_character_matrix_parses_json(self, mock_runtime_config):
        ai = OpenAITranslationClient(mock_runtime_config)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{\n  "characters": [{"name": "Neo", "gender": "male", "role": "The One"}],\n  "pronoun_rules": [{"pair": ["Neo", "Trinity"], "relationship": "lovers", "call": "anh - em"}]\n}\n```'
                    }
                }
            ]
        }
        ai._client.post = AsyncMock(return_value=mock_response)

        matrix = await ai.generate_character_matrix(
            {"title": "The Matrix", "roles": [{"actor": "Keanu", "character": "Neo"}]},
            ["Wake up, Neo..."],
        )
        assert len(matrix["characters"]) == 1
        assert matrix["characters"][0]["name"] == "Neo"
        assert matrix["characters"][0]["gender"] == "male"
        assert len(matrix["pronoun_rules"]) == 1
        assert matrix["pronoun_rules"][0]["call"] == "anh - em"


class TestSubtitleServiceCharacterMatrixCache:
    """Test Show-level caching in SubtitleService."""

    @patch("app.clients.plex_client.PlexServer")
    @pytest.mark.asyncio
    async def test_get_or_create_character_matrix_show_cache(self, mock_plex_cls, mock_runtime_config, tmp_path):
        mock_server = Mock()
        mock_plex_cls.return_value = mock_server
        service = SubtitleService(mock_runtime_config)
        service.plex_client.get_media_characters_context = Mock(return_value={
            "title": "Episode 1",
            "show_title": "Agatha",
            "parent_rating_key": "show_999",
            "roles": [{"actor": "Kathryn", "character": "Agatha"}],
        })
        service.translation_client.generate_character_matrix = AsyncMock(return_value={
            "characters": [{"name": "Agatha", "gender": "female", "role": "witch"}],
            "pronoun_rules": [],
        })

        # First call: generates matrix and caches at show_999
        res1 = await service.get_or_create_character_matrix("ep_1")
        assert res1["characters"][0]["name"] == "Agatha"
        assert service.translation_client.generate_character_matrix.await_count == 1

        # Second call for Episode 2 (same show_999): uses cached matrix!
        service.plex_client.get_media_characters_context = Mock(return_value={
            "title": "Episode 2",
            "show_title": "Agatha",
            "parent_rating_key": "show_999",
            "roles": [{"actor": "Kathryn", "character": "Agatha"}],
        })
        res2 = await service.get_or_create_character_matrix("ep_2")
        assert res2["characters"][0]["name"] == "Agatha"
        # generate_character_matrix was NOT called again!
        assert service.translation_client.generate_character_matrix.await_count == 1
