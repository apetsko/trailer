import os
import re
import sys
import json
import shutil
import hashlib
import argparse
import subprocess

try:
    import yaml
except ImportError:
    print("Ошибка: Для работы скрипта нужна библиотека PyYAML.")
    print("Пожалуйста, установите её командой: pip install pyyaml")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENCODED_DIR = os.path.join(BASE_DIR, 'encoded')

# Допустимое расхождение длительности итогового файла и суммы частей (сек.)
DURATION_TOLERANCE = 3.0
# Единый timescale для всех частей, чтобы concat не ломал тайминги
VIDEO_TIMESCALE = '90000'


# --------------------------------------------------------------------------
# Поиск инструментов
# --------------------------------------------------------------------------
def find_tool(name):
    """Ищет ffmpeg/ffprobe: сначала рядом со скриптом (Windows), затем в PATH."""
    for candidate in (name + '.exe', name):
        local = os.path.join(BASE_DIR, candidate)
        if os.path.isfile(local):
            return local
    return shutil.which(name) or name


FFMPEG = find_tool('ffmpeg')
FFPROBE = find_tool('ffprobe')


# --------------------------------------------------------------------------
# Время
# --------------------------------------------------------------------------
def parse_time(t_str):
    """
    Приводит время к формату HH:MM:SS[.дробь].
    Поддерживаются: 00.15.00, 00:15:00, 15.00 (MM.SS), 00.15.00.5 / 00.15.00,5 (с долями).
    """
    s = str(t_str).strip().replace(',', '.')
    if ':' in s:
        return s
    parts = s.split('.')
    if len(parts) >= 4:
        return f"{parts[0]}:{parts[1]}:{parts[2]}.{''.join(parts[3:])}"
    return ':'.join(parts)


def time_str_to_seconds(t_str):
    """Секунды из HH:MM:SS(.ms), MM:SS(.ms) или SS(.ms). Бросает ValueError при мусоре."""
    parts = [float(p) for p in str(t_str).strip().replace(',', '.').split(':')]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


# --------------------------------------------------------------------------
# Запуск ffmpeg / ffprobe
# --------------------------------------------------------------------------
def run_ffmpeg(cmd, what):
    """Запускает ffmpeg, проверяет код возврата, при ошибке показывает хвост stderr."""
    full = [cmd[0], '-hide_banner', '-loglevel', 'error'] + cmd[1:]
    try:
        res = subprocess.run(full, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        print(f"  ❌ Не найден ffmpeg ({cmd[0]}). Установите его или положите рядом со скриптом.")
        return False
    if res.returncode != 0:
        print(f"  ❌ Ошибка ffmpeg при операции: {what}")
        tail = '\n'.join(res.stderr.strip().splitlines()[-8:])
        if tail:
            print("     " + tail.replace('\n', '\n     '))
        return False
    return True


def probe(path, *extra):
    cmd = [FFPROBE, '-v', 'error', '-of', 'json'] + list(extra) + [path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, encoding='utf-8', errors='replace')
        return json.loads(res.stdout or '{}')
    except Exception:
        return {}


def get_duration(path):
    """Длительность файла в секундах (0.0, если файл битый или пустой)."""
    if not os.path.isfile(path):
        return 0.0
    data = probe(path, '-show_entries', 'format=duration')
    try:
        return float(data.get('format', {}).get('duration', 0.0))
    except (TypeError, ValueError):
        return 0.0


def has_audio_stream(path):
    data = probe(path, '-select_streams', 'a', '-show_entries', 'stream=index')
    return bool(data.get('streams'))


def _valid_fps(value):
    try:
        num, den = str(value).split('/')
        return float(den) != 0 and float(num) > 0
    except Exception:
        return False


def get_video_properties(video_path):
    data = probe(video_path, '-show_streams')
    props = {}
    for stream in data.get('streams', []):
        ctype = stream.get('codec_type')
        if ctype == 'video' and 'v_index' not in props:
            # Пропускаем обложки (attached_pic)
            if stream.get('disposition', {}).get('attached_pic'):
                continue
            fps = stream.get('r_frame_rate')
            if not _valid_fps(fps):
                fps = stream.get('avg_frame_rate')
            if not _valid_fps(fps):
                fps = '24/1'
            sar = str(stream.get('sample_aspect_ratio', '1:1'))
            if sar in ('N/A', '0:1', ''):
                sar = '1:1'
            props.update({
                'v_index': stream['index'],
                'width': stream['width'],
                'height': stream['height'],
                'fps': fps,
                'sar': sar,
                'v_codec': stream.get('codec_name', 'h264'),
                'pix_fmt': stream.get('pix_fmt', 'yuv420p'),
            })
        elif ctype == 'audio' and 'a_index' not in props:
            props.update({
                'a_index': stream['index'],
                'a_codec': stream.get('codec_name', 'aac'),
                'a_sample_rate': str(stream.get('sample_rate', '48000')),
                'a_channels': int(stream.get('channels', 2)),
            })

    if 'v_index' not in props:
        print(f"Ошибка: в файле {video_path} не найден видеопоток.")
        return None
    props['has_audio'] = 'a_index' in props
    if not props['has_audio']:
        props.update({'a_codec': 'aac', 'a_sample_rate': '48000', 'a_channels': 2})
    return props


# --------------------------------------------------------------------------
# Кодеки
# --------------------------------------------------------------------------
def get_ffmpeg_encoder(codec_name):
    mapping = {
        'h264': 'libx264',
        'hevc': 'libx265',
        'h265': 'libx265',
        'mpeg4': 'mpeg4',
        'vp8': 'libvpx',
        'vp9': 'libvpx-vp9',
        'av1': 'libaom-av1',
    }
    return mapping.get(codec_name.lower(), 'libx264')


def get_ffmpeg_audio_encoder(codec_name):
    mapping = {
        'mp3': 'libmp3lame',
        'aac': 'aac',
        'ac3': 'ac3',
        'eac3': 'eac3',
        'opus': 'libopus',
        'vorbis': 'libvorbis',
        'flac': 'flac',
    }
    return mapping.get(codec_name.lower(), 'aac')


def get_video_quality_args(encoder_name):
    if encoder_name == 'libx264':
        return ['-preset', 'fast', '-crf', '20']
    if encoder_name == 'libx265':
        return ['-preset', 'fast', '-crf', '26']
    if encoder_name in ('libvpx', 'libvpx-vp9', 'libaom-av1'):
        return ['-crf', '30', '-b:v', '0']
    return ['-q:v', '3']


def video_args(props):
    """Параметры видеокодирования — одинаковы для вставок и перекодируемых фрагментов."""
    enc = get_ffmpeg_encoder(props['v_codec'])
    args = ['-c:v', enc] + get_video_quality_args(enc)
    args += ['-pix_fmt', props['pix_fmt'], '-r', str(props['fps']),
             '-video_track_timescale', VIDEO_TIMESCALE]
    return args


def audio_args(props):
    if not props['has_audio']:
        return ['-an']
    enc = get_ffmpeg_audio_encoder(props['a_codec'])
    args = ['-c:a', enc, '-ar', str(props['a_sample_rate']), '-ac', str(props['a_channels'])]
    if enc != 'flac':
        args += ['-b:a', '192k' if props['a_channels'] <= 2 else '384k']
    return args


# --------------------------------------------------------------------------
# Вставки (рекламные ролики)
# --------------------------------------------------------------------------
def get_encoded_ad_path(ad_file, props):
    """Путь к кэшу перекодированной вставки. Ключ включает формат фильма и версию файла вставки."""
    st = os.stat(ad_file)
    key = (f"{props['width']}x{props['height']}_{props['fps']}_{props['sar']}_{props['pix_fmt']}_"
           f"{props['v_codec']}_{props['has_audio']}_{props['a_codec']}_"
           f"{props['a_channels']}_{props['a_sample_rate']}_{st.st_size}_{int(st.st_mtime)}")
    h = hashlib.md5(key.encode()).hexdigest()[:10]
    base = os.path.splitext(os.path.basename(ad_file))[0]
    return os.path.join(ENCODED_DIR, f"{base}_{h}.mkv")


def normalize_ad(props, ad_file, output_file):
    """Перекодирует вставку в точный формат фильма. Возвращает True при успехе."""
    w, h = props['width'], props['height']
    ad_has_audio = has_audio_stream(ad_file)

    cmd = [FFMPEG, '-y', '-i', ad_file]
    use_silence = props['has_audio'] and not ad_has_audio
    if use_silence:
        # Без звука concat испортит дорожку всего фильма — подставляем тишину
        layout = 'mono' if props['a_channels'] == 1 else 'stereo'
        cmd += ['-f', 'lavfi', '-i', f"anullsrc=r={props['a_sample_rate']}:cl={layout}"]

    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar={props['sar'].replace(':', '/')}")
    cmd += ['-map', '0:v:0']
    if props['has_audio']:
        cmd += ['-map', '1:a:0' if use_silence else '0:a:0']
    cmd += ['-vf', vf]
    cmd += video_args(props) + audio_args(props)
    if props['has_audio'] and not use_silence:
        cmd += ['-af', 'aresample=async=1:first_pts=0']
    if use_silence:
        cmd += ['-shortest']

    tmp = output_file[:-4] + '.part.mkv'
    cmd.append(tmp)
    if not run_ffmpeg(cmd, f"перекодирование вставки '{ad_file}'") or get_duration(tmp) <= 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    os.replace(tmp, output_file)
    return True


# --------------------------------------------------------------------------
# Фрагменты фильма
# --------------------------------------------------------------------------
def cut_segment(props, main_video, start, duration, part_name, precise):
    """
    precise=True  — фрагмент перекодируется в тот же формат, что и вставки (точный и совместимый).
    precise=False — быстрый -c copy (по ключевым кадрам, возможны проблемы совместимости).
    """
    cmd = [FFMPEG, '-y', '-fflags', '+genpts', '-ss', start, '-i', main_video]
    if duration is not None:
        cmd += ['-t', f"{duration:.3f}"]
    # Берём только один видео- и один аудиопоток: субтитры/вложения ломают concat
    cmd += ['-map', f"0:{props['v_index']}"]
    if props['has_audio']:
        cmd += ['-map', f"0:{props['a_index']}"]
    cmd += ['-sn', '-dn']
    if precise:
        cmd += video_args(props) + audio_args(props)
        if props['has_audio']:
            cmd += ['-af', 'aresample=async=1:first_pts=0']
    else:
        cmd += ['-c', 'copy', '-avoid_negative_ts', 'make_zero']
    cmd.append(part_name)
    return run_ffmpeg(cmd, f"вырезка фрагмента {start}") and get_duration(part_name) > 0


# --------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------
def concat_list_path_escape(path):
    return os.path.abspath(path).replace('\\', '/').replace("'", "'\\''")


def build_once(job_name, main_video, sequence, job_index, props, precise):
    """Одна попытка сборки. Возвращает True, если результат прошёл проверку."""
    tmp_dir = os.path.join(BASE_DIR, f".tmp_job_{job_index}")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        parts = []
        part_index = 0

        for item in sequence:
            item_clean = re.sub(r'^(clip:\s*|-\s*)', '', str(item), flags=re.IGNORECASE).strip()
            match = re.fullmatch(r'([\d.:,]+)\s*-\s*([\d.:,]+|end)', item_clean, re.IGNORECASE)

            if match:
                start = parse_time(match.group(1))
                end_raw = match.group(2).lower()
                try:
                    start_sec = time_str_to_seconds(start)
                    duration = None
                    if end_raw != 'end':
                        duration = time_str_to_seconds(parse_time(end_raw)) - start_sec
                        if duration <= 0:
                            print(f"  ⚠️ Неверный диапазон времени: {item_clean}. Пропускаю.")
                            continue
                except ValueError:
                    print(f"  ⚠️ Не удалось разобрать время: '{item_clean}'. Пропускаю.")
                    continue

                part_name = os.path.join(tmp_dir, f"part_{part_index}.mkv")
                mode = "перекодирование" if precise else "copy"
                print(f"  ⏳ Вырезаю фрагмент ({mode}): {item_clean}...")
                if not cut_segment(props, main_video, start, duration, part_name, precise):
                    print("  ❌ Не удалось вырезать фрагмент (возможно, время за пределами фильма).")
                    return False
                parts.append(part_name)
                part_index += 1
            else:
                ad_file = item_clean
                if not os.path.isfile(ad_file):
                    print(f"  ⚠️ Ролик '{ad_file}' не найден! Пропускаю.")
                    continue
                os.makedirs(ENCODED_DIR, exist_ok=True)
                encoded = get_encoded_ad_path(ad_file, props)
                if os.path.exists(encoded) and get_duration(encoded) > 0:
                    print(f"  ♻️  Ролик '{ad_file}' берётся из кэша: {os.path.basename(encoded)}")
                else:
                    print(f"  ⚙️  Подгоняю ролик '{ad_file}' под формат фильма...")
                    if not normalize_ad(props, ad_file, encoded):
                        return False
                parts.append(encoded)

        if not parts:
            print("  ❌ Не найдено валидных фрагментов для склейки.")
            return False

        concat_file = os.path.join(tmp_dir, "concat.txt")
        with open(concat_file, 'w', encoding='utf-8') as f:
            for p in parts:
                f.write(f"file '{concat_list_path_escape(p)}'\n")

        expected = sum(get_duration(p) for p in parts)

        print(f"  🔄 Склеиваю {job_name}...")
        cmd = [FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
               '-map', '0', '-c', 'copy', '-fflags', '+genpts', job_name]
        if not run_ffmpeg(cmd, "склейка"):
            return False

        # Проверка результата
        actual = get_duration(job_name)
        if actual <= 0:
            print("  ❌ Итоговый файл пустой или не читается.")
            return False
        if abs(actual - expected) > max(DURATION_TOLERANCE, expected * 0.02):
            print(f"  ❌ Длительность результата ({actual:.1f}с) не совпадает с суммой частей ({expected:.1f}с).")
            return False
        # Пробное декодирование начала и конца склеенного файла
        if not run_ffmpeg([FFMPEG, '-v', 'error', '-i', job_name, '-t', '30', '-f', 'null', '-'],
                          "проверка декодирования"):
            return False
        print(f"  ✔️ Проверка пройдена: {actual:.1f}с")
        return True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def process_job(job_name, main_video, sequence, job_index, mode='precise'):
    if not main_video or not os.path.exists(main_video):
        print(f"[{job_name}] ❌ Ошибка: Главный фильм '{main_video}' не найден.")
        return False
    if not isinstance(sequence, list) or not sequence:
        print(f"[{job_name}] ❌ Ошибка: пустая или неверная последовательность 'sequence'.")
        return False

    output_dir = os.path.dirname(job_name)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"\n🎬 Начинаю сборку: {job_name}")
    print(f"  🔍 Анализирую параметры фильма '{main_video}'...")
    props = get_video_properties(main_video)
    if not props:
        print(f"[{job_name}] ❌ Не удалось получить параметры фильма через ffprobe.")
        return False

    audio_info = (f"{props['a_codec']} {props['a_channels']}ch {props['a_sample_rate']}Hz"
                  if props['has_audio'] else "без звука")
    print(f"  📋 Формат: {props['v_codec']} {props['width']}x{props['height']} @ {props['fps']}, {audio_info}")

    ok = False
    if mode == 'copy':
        print("  📋 Режим copy: фрагменты вырезаются без перекодирования (быстро).")
        ok = build_once(job_name, main_video, sequence, job_index, props, precise=False)
        if not ok:
            print("  ↩️  Быстрый режим не дал корректного файла. Перезапускаю в режиме precise...")
    if not ok:
        if mode != 'copy':
            print("  📋 Режим precise: фрагменты фильма перекодируются в единый формат (надёжно).")
        ok = build_once(job_name, main_video, sequence, job_index, props, precise=True)

    if ok:
        print(f"🎉 Готово: {job_name} сохранен!")
    else:
        print(f"[{job_name}] ❌ Сборка не удалась.")
        if os.path.exists(job_name):
            try:
                os.remove(job_name)
            except OSError:
                pass
    return ok


def main():
    parser = argparse.ArgumentParser(description="Скрипт для пакетной нарезки и склейки трейлеров (через YAML).")
    parser.add_argument("-c", "--config", default="config.yaml", help="Путь к файлу конфигурации")
    parser.add_argument("-m", "--mode", choices=['precise', 'copy'], default=None,
                        help="precise — перекодировать фрагменты (надёжно, по умолчанию); "
                             "copy — быстро без перекодирования с откатом на precise при ошибке")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Файл '{args.config}' не найден. Пожалуйста, создайте его.")
        return 1

    with open(args.config, 'r', encoding='utf-8') as f:
        try:
            jobs = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Ошибка при чтении YAML файла: {e}")
            return 1

    if not isinstance(jobs, list):
        print("Ошибка: YAML файл должен содержать массив (список) задач.")
        return 1

    failed = 0
    for job_index, job in enumerate(jobs):
        if not isinstance(job, dict):
            print(f"Задача #{job_index + 1}: неверный формат, пропускаю.")
            failed += 1
            continue
        job_name = job.get("output", f"output_{job_index}.mkv")
        mode = args.mode or job.get("mode", "precise")
        if mode not in ('precise', 'copy'):
            print(f"[{job_name}] ⚠️ Неизвестный mode '{mode}', использую precise.")
            mode = 'precise'
        if not process_job(job_name, job.get("movie"), job.get("sequence", []), job_index, mode):
            failed += 1

    if failed:
        print(f"\n⚠️ Задач с ошибками: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
