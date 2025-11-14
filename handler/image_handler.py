import logging
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from handler.constants import (DEFAULT_IMAGE_SIZE, FEEDS_FOLDER, FRAME_FOLDER,
                               IMAGE_FOLDER, NAME_OF_FRAME, NEW_IMAGE_FOLDER,
                               NUMBER_PIXELS_IMAGE, RGBA_COLOR_SETTINGS,
                               VERTICAL_OFFSET)
from handler.decorators import time_of_function
from handler.exceptions import DirectoryCreationError, EmptyFeedsListError
from handler.feeds import FEEDS
from handler.logging_config import setup_logging
from handler.mixins import FileMixin

setup_logging()


class XMLImage(FileMixin):
    """
    Класс, предоставляющий интерфейс
    для работы с изображениями.
    """

    def __init__(
        self,
        feeds_folder: str = FEEDS_FOLDER,
        frame_folder: str = FRAME_FOLDER,
        image_folder: str = IMAGE_FOLDER,
        new_image_folder: str = NEW_IMAGE_FOLDER,
        feeds_list: tuple[str, ...] = FEEDS,
        number_pixels_image: int = NUMBER_PIXELS_IMAGE
    ) -> None:
        self.frame_folder = frame_folder
        self.feeds_folder = feeds_folder
        self.image_folder = image_folder
        self.new_image_folder = new_image_folder
        self.feeds_list = feeds_list
        self.number_pixels_image = number_pixels_image
        self._existing_image_offers: set = set()
        self._existing_framed_offers: set = set()

    def _get_image_data(self, url: str) -> tuple:
        """
        Защищенный метод, загружает данные изображения
        и возвращает (image_data, image_format).
        """
        response_content = None
        try:
            response = requests.get(url)
            response.raise_for_status()
            response_content = response.content
            image = Image.open(BytesIO(response_content))
            image_format = image.format.lower() if image.format else None
            return response_content, image_format
        except requests.exceptions.RequestException as error:
            logging.error('Ошибка сети при загрузке URL %s: %s', url, error)
            return None, None
        except IOError as error:
            logging.error(
                'Pillow не смог распознать изображение из URL %s: %s',
                url,
                error
            )
            return None, None
        except Exception as error:
            logging.error(
                'Непредвиденная ошибка при обработке изображения %s: %s',
                url,
                error
            )
            return None, None

    def _get_image_filename(
        self,
        offer_id: str,
        image_data: bytes,
        image_format: str
    ) -> str:
        """Защищенный метод, создает имя файла с изображением."""
        if not image_data or not image_format:
            return ''
        return f'{offer_id}.{image_format}'

    def _build_offers_set(self, folder: str, target_set: set):
        """Защищенный метод, строит множество всех существующих офферов."""
        try:
            filenames_list = self._get_filenames_list(folder)
            for file_name in filenames_list:
                offer_image = file_name.split('.')[0]
                if offer_image:
                    target_set.add(offer_image)

            logging.info(
                'Построен кэш для %s файлов',
                len(target_set)
            )
        except EmptyFeedsListError:
            raise
        except DirectoryCreationError:
            raise
        except Exception as error:
            logging.error(
                'Неожиданная ошибка при сборе множества '
                'скачанных изображений: %s',
                error
            )
            raise

    def _save_image(
        self,
        image_data: bytes,
        folder_path: Path,
        image_filename: str
    ):
        """Защищенный метод, сохраняет изображение по указанному пути."""
        if not image_data:
            return
        try:
            file_path = folder_path / image_filename
            with open(file_path, 'wb') as f:
                f.write(image_data)
            logging.debug('Изображение сохранено: %s', file_path)
        except Exception as error:
            logging.error(
                'Ошибка при сохранении %s: %s',
                image_filename,
                error
            )

    @time_of_function
    def get_images(self):
        """Метод получения и сохранения изображений из xml-файла."""
        total_offers_processed = 0
        offers_with_images = 0
        images_downloaded = 0
        offers_skipped_existing = 0
        images_skipped_no_photo = 0

        try:
            self._build_offers_set(
                self.image_folder,
                self._existing_image_offers
            )
        except (DirectoryCreationError, EmptyFeedsListError):
            logging.warning(
                'Директория с изображениями отсутствует. Первый запуск'
            )
        try:
            file_name_list = self._get_filenames_list(self.feeds_folder)
            for file_name in file_name_list:
                tree = self._get_tree(file_name, self.feeds_folder)
                root = tree.getroot()
                offers = root.findall('.//offer')

                if not offers:
                    logging.debug('В файле %s не найдено offers', file_name)
                    continue

                for offer in offers:
                    offer_id = offer.get('id')
                    total_offers_processed += 1

                    picture = offer.find('picture')
                    if picture is None:
                        continue

                    offer_image = picture.text
                    if not offer_image:
                        continue

                    if 'no_photo' in offer_image:
                        images_skipped_no_photo += 1
                        continue

                    offers_with_images += 1

                    if str(offer_id) in self._existing_image_offers:
                        offers_skipped_existing += 1
                        continue

                    image_data, image_format = self._get_image_data(
                        offer_image
                    )
                    image_filename = self._get_image_filename(
                        offer_id,
                        image_data,
                        image_format
                    )
                    folder_path = self._make_dir(self.image_folder)
                    self._save_image(
                        image_data,
                        folder_path,
                        image_filename
                    )
                    images_downloaded += 1
            logging.info(
                '\nВсего обработано фидов - %s'
                '\nВсего обработано офферов - %s'
                '\nВсего офферов с подходящими изображениями - %s'
                '\nВсего изображений скачано - %s'
                '\nПропущено изображений no_photo - %s'
                '\nПропущено офферов с уже скачанными изображениями - %s',
                len(file_name_list),
                total_offers_processed,
                offers_with_images,
                images_downloaded,
                images_skipped_no_photo,
                offers_skipped_existing
            )
        except Exception as error:
            logging.error(
                'Неожиданная ошибка при получении изображений: %s',
                error
            )

    @time_of_function
    def add_frame(self):
        """Метод форматирует изображения и добавляет рамку."""
        file_path = self._make_dir(self.image_folder)
        frame_path = self._make_dir(self.frame_folder)
        new_file_path = self._make_dir(self.new_image_folder)
        images_names_list = self._get_filenames_list(self.image_folder)
        total_framed_images = 0
        total_failed_images = 0
        skipped_images = 0

        try:
            self._build_offers_set(
                self.new_image_folder,
                self._existing_framed_offers
            )
        except (DirectoryCreationError, EmptyFeedsListError):
            logging.warning(
                'Директория с форматированными изображениями отсутствует. '
                'Первый запуск'
            )
        try:
            for image_name in images_names_list:
                if image_name.split('.')[0] in self._existing_framed_offers:
                    skipped_images += 1
                    continue
                try:
                    with Image.open(file_path / image_name) as image:
                        image = image.convert('RGBA')
                        image.load()
                except Exception as e:
                    total_failed_images += 1
                    logging.error(
                        f'Ошибка загрузки изображения {image_name}: {e}'
                    )
                    continue

                with Image.open(frame_path / NAME_OF_FRAME) as frame:
                    frame_resized = frame.resize(DEFAULT_IMAGE_SIZE)

                image_width, image_height = DEFAULT_IMAGE_SIZE
                new_image_width = image_width - self.number_pixels_image
                new_image_height = image_height - self.number_pixels_image

                resized_image = image.resize(
                    (new_image_width, new_image_height)
                )

                final_image = Image.new(
                    'RGBA',
                    DEFAULT_IMAGE_SIZE,
                    RGBA_COLOR_SETTINGS
                )

                x_position = (image_width - new_image_width) // 2
                y_position = (
                    image_height - new_image_height
                ) // 2 + VERTICAL_OFFSET
                final_image.paste(
                    resized_image,
                    (x_position, y_position),
                    resized_image
                )
                final_image.paste(frame_resized, (0, 0), frame_resized)
                final_image = final_image.convert('RGB')
                final_image.save(
                    new_file_path / f'{image_name.split('.')[0]}.png',
                    'PNG'
                )
                total_framed_images += 1
            logging.info(
                '\nКоличество изображений, к которым добавлена рамка - %s'
                '\nКоличество уже обрамленных изображений - %s'
                '\nКоличество изображений обрамленных неудачно - %s',
                total_framed_images,
                skipped_images,
                total_failed_images
            )
        except Exception as error:
            logging.error(
                'Критическая ошибка в процессе обрамления: %s',
                error
            )
            raise
