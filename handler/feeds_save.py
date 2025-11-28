import logging
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv

from handler.constants import ATTEMPTION_LOAD_FEED, FEEDS_FOLDER, MAX_WORKERS
from handler.decorators import retry_on_network_error, time_of_function
from handler.exceptions import (EmptyFeedsListError, EmptyXMLError,
                                InvalidXMLError)
from handler.feeds import FEEDS
from handler.logging_config import setup_logging
from handler.mixins import FileMixin

setup_logging()
logger = logging.getLogger(__name__)


class XMLSaver(FileMixin):
    """
    Класс, предоставляющий интерфейс для скачивания,
    валидации и сохранения фида в xml-файл.
    """
    load_dotenv()

    def __init__(
        self,
        feeds_list: tuple[str, ...] = FEEDS,
        feeds_folder: str = FEEDS_FOLDER,
        max_workers: int = MAX_WORKERS,
        max_load_attempt: int = ATTEMPTION_LOAD_FEED
    ) -> None:
        if not feeds_list:
            logging.error('Не передан список фидов.')
            raise EmptyFeedsListError('Список фидов пуст.')

        self.feeds_list = feeds_list
        self.feeds_folder = feeds_folder
        self.max_workers = max_workers
        self.max_load_attempt = max_load_attempt

    @retry_on_network_error(max_attempts=3, delays=(2, 5, 10))
    def _get_file(self, feed: str):
        """Защищенный метод, получает фид по ссылке."""
        try:
            username = os.getenv('XML_FEED_USERNAME')
            password = os.getenv('XML_FEED_PASSWORD')
            response = requests.get(
                feed,
                auth=(username, password),
                stream=True,
                timeout=(30, 300)
            )

            if response.status_code == requests.codes.ok:
                return response

            else:
                logging.error(
                    'HTTP ошибка %s при загрузке %s',
                    response.status_code,
                    feed
                )
                raise requests.exceptions.HTTPError(
                    f'HTTP {response.status_code} для {feed}'
                )

        except requests.RequestException as error:
            logger.bot_event('Ошибка при загрузке %s: %s', feed, error)
            raise

    def _get_filename(self, feed: str) -> str:
        """Защищенный метод, формирующий имя xml-файлу."""
        return feed.split('/')[-1]

    def _indent(self, elem, level=0) -> None:
        """Защищенный метод, расставляет правильные отступы в XML файлах."""
        i = '\n' + level * '  '
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + '  '
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for child in elem:
                self._indent(child, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    def _validate_xml(self, xml_content: bytes):
        """
        Валидирует XML.
        Возвращает декодированное содержимое и кодировку.
        """
        if not xml_content.strip():
            raise EmptyXMLError('XML пуст')
        encoding = 'utf-8'
        try:
            declaration = xml_content[:100].decode('ascii', errors='ignore')
            if 'encoding=' in declaration:
                match = re.search(r'encoding=[\'"]([^\'"]+)[\'"]', declaration)
                if match:
                    encoding = match.group(1).lower()
        except Exception as error:
            logging.warning(
                'Не удалось определить кодировку из декларации: %s',
                error
            )
        try:
            decoded_content = xml_content.decode(encoding)
        except UnicodeDecodeError:
            try:
                decoded_content = xml_content.decode('utf-8')
                encoding = 'utf-8'
            except UnicodeDecodeError:
                raise InvalidXMLError('Не удалось декодировать XML')
        try:
            ET.fromstring(decoded_content)
        except ET.ParseError as e:
            raise InvalidXMLError(f'XML содержит синтаксические ошибки: {e}')
        return decoded_content, encoding

    def _process_single_feed(self, feed: str, folder_path: Path):
        """Обрабатывает один фид: скачивает, валидирует и сохраняет."""
        file_name = self._get_filename(feed)
        file_path = folder_path / file_name
        try:
            response = self._get_file(feed)
            xml_content = response.content
            decoded_content, encoding = self._validate_xml(xml_content)
            xml_tree = ET.fromstring(decoded_content)
            self._indent(xml_tree)
            tree = ET.ElementTree(xml_tree)
            with open(file_path, 'wb') as file:
                tree.write(file, encoding=encoding, xml_declaration=True)
            logging.info('Файл %s успешно сохранен', file_name)
            return True
        except requests.exceptions.RequestException as error:
            logging.warning('Фид %s не получен: %s', file_name, error)
            return False
        except (EmptyXMLError, InvalidXMLError) as error:
            logging.error('Ошибка валидации XML %s: %s', file_name, error)
            return False
        except Exception as error:
            logging.error(
                'Ошибка обработки файла %s: %s',
                file_name,
                error
            )
            raise

    @time_of_function
    def save_xml(self) -> None:
        """Метод, сохраняющий фиды в xml-файлы."""
        total_files: int = len(self.feeds_list)
        saved_files = 0
        folder_path = self._make_dir(self.feeds_folder)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_feed = {
                executor.submit(
                    self._process_single_feed,
                    feed,
                    folder_path
                ): feed
                for feed in self.feeds_list
            }
            for future in as_completed(future_to_feed):
                feed = future_to_feed[future]
                try:
                    if future.result():
                        saved_files += 1
                except Exception as exc:
                    logging.error(
                        'Фид %s сгенерировал исключение: %s', feed, exc)
        logger.bot_event(
            'Успешно записано %s файлов из %s.',
            saved_files,
            total_files
        )
