import logging
import xml.etree.ElementTree as ET

from handler.allowed_urls import ALLOWED_URLS
from handler.constants import (ADDRESS_FTP_IMAGES, FEEDS_FOLDER,
                               NEW_FEEDS_FOLDER, NEW_IMAGE_FOLDER)
from handler.decorators import time_of_function, try_except
from handler.feeds import FEEDS
from handler.logging_config import setup_logging
from handler.mixins import FileMixin

setup_logging()
logger = logging.getLogger(__name__)


class FeedHandler(FileMixin):
    """
    Класс, предоставляющий интерфейс
    для обработки xml-файлов.
    """

    def __init__(
        self,
        filename: str,
        feeds_folder: str = FEEDS_FOLDER,
        new_feeds_folder: str = NEW_FEEDS_FOLDER,
        new_image_folder: str = NEW_IMAGE_FOLDER,
        feeds_list: tuple[str, ...] = FEEDS
    ) -> None:
        self.filename = filename
        self.feeds_folder = feeds_folder
        self.new_feeds_folder = new_feeds_folder
        self.feeds_list = feeds_list
        self.new_image_folder = new_image_folder
        self._root = None
        self._is_modified = False

    @property
    def root(self):
        """Ленивая загрузка корневого элемента."""
        if self._root is None:
            self._root = self._get_root(self.filename, self.feeds_folder)
        return self._root

    @try_except
    def change_available(self, offers_id_list: list, flag: str):
        offers = self.root.findall('.//offer')

        if not offers:
            logging.error('В файле %s не найдено offers', self.filename)
            raise

        for offer in offers:
            offer_id = offer.get('id')
            if offer_id in offers_id_list:
                offer.set('available', flag)
                self._is_modified = True
        return self

    @time_of_function
    @try_except
    def add_custom_label(
        self,
        custom_label: dict[str, dict],
    ):
        """
        Метод, подставляющий в фиды данные
        из настраиваемого словаря CUSTOM_LABEL.
        """
        offers = self.root.findall('.//offer')

        if not offers:
            logging.error('В файле %s не найдено offers', self.filename)
            raise

        for offer in offers:
            offer_name_text = offer.findtext('name')
            offer_url_text = offer.findtext('url')
            offer_id = offer.get('id')
            if None in (
                offer_name_text,
                offer_url_text,
                offer_id
            ):
                continue
            existing_nums = set()
            for element in offer.findall('*'):
                if element.tag.startswith('custom_label_'):
                    try:
                        existing_nums.add(
                            int(element.tag.split('_')[-1]))
                    except ValueError:
                        continue
            for label_name, conditions in custom_label.items():
                name_match = any(
                    sub.lower() in offer_name_text.lower()
                    for sub in conditions.get('name', [])
                )
                url_match = any(
                    sub.lower() in offer_url_text.lower()
                    for sub in conditions.get('url', [])
                )
                id_match = offer_id in conditions.get('id', [])
                if name_match or url_match or id_match:
                    next_num = 0
                    while next_num in existing_nums:
                        next_num += 1
                    existing_nums.add(next_num)
                    ET.SubElement(
                        offer, f'custom_label_{next_num}'
                    ).text = label_name
                    self._is_modified = True
        return self

    @time_of_function
    def replace_images(self):
        """Метод, подставляющий в фиды новые изображения."""
        deleted_images = 0
        input_images = 0
        try:
            image_dict = self._get_files_dict(self.new_image_folder)

            offers = self.root.findall('.//offer')
            for offer in offers:
                offer_id = offer.get('id')
                if not offer_id:
                    continue

                if offer_id in image_dict:
                    pictures = offer.findall('picture')
                    for picture in pictures:
                        offer.remove(picture)
                    deleted_images += len(pictures)

                    picture_tag = ET.SubElement(offer, 'picture')
                    picture_tag.text = (
                        f'{ADDRESS_FTP_IMAGES}/{image_dict[offer_id]}'
                    )
                    input_images += 1
                    self._is_modified = True
            logging.info(
                '\nКоличество удаленных изображений - %s'
                '\nКоличество добавленных изображений - %s',
                deleted_images,
                input_images
            )
            return self
        except Exception as error:
            logging.error('Ошибка в image_replacement: %s', error)
            raise

    def url_filter(
        self,
        param: str = 'referrer=reattribution%3D1'
    ):
        suitable_offers = 0
        deleted_offers = 0
        to_remove = []
        try:
            offers = self.root.findall('.//offer')
            for offer in offers:
                url_elem = offer.find('url')
                if url_elem is None:
                    continue

                url = url_elem.text
                if not url:
                    continue

                url_without_parameters = url.split('?')[0]

                if url_without_parameters not in ALLOWED_URLS:
                    to_remove.append(offer)
                    deleted_offers += 1
                    continue

                new_url = url_without_parameters + '?' + param
                url_elem.text = new_url
                suitable_offers += 1

            for offer in to_remove:
                parent = offer.getparent()
                if parent is not None:
                    parent.remove(offer)

                new_filename = f'dyn_{self.filename}'
            logging.info(
                'Подходищие urls в фиде %s - %s',
                self.filename,
                suitable_offers
            )
            logging.info(
                'Удалено неподходящих urls в фиде %s - %s',
                self.filename,
                deleted_offers
            )

            self._save_xml(self.root, 'join_feeds', new_filename)
            logging.info('Копия фида сохранена - %s', new_filename)
        except Exception as error:
            logging.error('Неожиданная ошибка: %s', error)
            raise

    def save(self, prefix: str = 'new'):
        """Метод сохраняет файл, если были изменения."""
        try:
            if not self._is_modified:
                logger.info(
                    'Изменений нет — файл %s не сохранён',
                    self.filename
                )
                return self

            new_filename = f'{prefix}_{self.filename}'

            self._save_xml(self.root, self.new_feeds_folder, new_filename)
            logger.info('Файл сохранён как %s', new_filename)

            self._is_modified = False
            return self
        except Exception as error:
            logging.error(
                'Неожиданная ошибка при сохранении файла %s: %s',
                self.filename,
                error
            )
            raise
