from calibre.customize import InterfaceActionBase


class AniListMangaSync(InterfaceActionBase):
    name = 'AniList Manga Sync'
    description = ('Syncs manga reading progress to AniList at chapter level, from Kobo percent read '
                   'plus each volume\'s chapter range. Includes "Link to AniList".')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'coder-eb'
    version = (0, 2, 0)
    minimum_calibre_version = (5, 0, 0)
    actual_plugin = 'calibre_plugins.anilist_calibre_plugin.action:AniListAction'

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.anilist_calibre_plugin.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
