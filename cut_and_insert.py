import os
import subprocess
import re
import argparse
import sys
import json
import hashlib

try:
    import yaml
except ImportError:
    print("Ошибка: Для работы скрипта нужна библиотека PyYAML.")
    print("Пожалуйста, установите её командой: pip install pyyaml")
    sys.exit(1)

def parse_time(t_str):
    return t_str.replace('.', ':').strip()

def time_str_to_seconds(t_str):
    s = str(t_str).strip()
    s = s.replace(',', '.')
    # Accept formats: HH:MM:SS(.ms), MM:SS(.ms), SS(.ms)
    parts = s.split(':')
    try:
        parts = [float(p) for p in parts]
    except:
        return 0.0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]
def get_video_properties(video_path):
    cmd = [
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)
        
        props = {}
        for stream in data.get('streams', []):
            if stream['codec_type'] == 'video' and 'width' not in props:
                props['width'] = stream['width']
                props['height'] = stream['height']
                props['fps'] = stream.get('r_frame_rate', '24/1')
                props['v_codec'] = stream.get('codec_name', 'h264')
                props['pix_fmt'] = stream.get('pix_fmt', 'yuv420p')
            elif stream['codec_type'] == 'audio' and 'a_codec' not in props:
                props['a_codec'] = stream.get('codec_name', 'aac')
                props['a_sample_rate'] = stream.get('sample_rate', '48000')
                props['a_channels'] = stream.get('channels', 2)
        
        # Если вдруг аудио вообще нет в фильме, зададим дефолт
        if 'a_codec' not in props:
            props['a_codec'] = 'aac'
            props['a_sample_rate'] = '48000'
            props['a_channels'] = 2
            
        return props
    except Exception as e:
        print(f"Ошибка при получении свойств видео {video_path}: {e}")
    return None

def get_ffmpeg_encoder(codec_name):
    """Маппинг имён кодеков ffprobe -> имена энкодеров ffmpeg."""
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
    """Маппинг имён аудио-кодеков ffprobe -> имена энкодеров ffmpeg."""
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
    """Возвращает аргументы качества для конкретного видео-энкодера."""
    if encoder_name == 'libx264':
        return ['-preset', 'fast', '-crf', '22']
    elif encoder_name == 'libx265':
        return ['-preset', 'fast', '-crf', '28']
    elif encoder_name == 'mpeg4':
        return ['-q:v', '4']
    elif encoder_name in ('libvpx', 'libvpx-vp9'):
        return ['-crf', '30', '-b:v', '0']
    elif encoder_name == 'libaom-av1':
        return ['-crf', '30', '-b:v', '0']
    else:
        return ['-q:v', '4']

def get_encoded_ad_path(ad_file, main_props):
    """Генерирует путь к кэшированному перекодированному ролику в папке encoded/."""
    ad_basename = os.path.splitext(os.path.basename(ad_file))[0]
    # Формируем уникальный суффикс из параметров фильма
    fmt_key = f"{main_props['width']}x{main_props['height']}_{main_props['v_codec']}_{main_props['a_codec']}_{main_props['a_channels']}ch_{main_props['a_sample_rate']}hz"
    # Короткий хэш для уникальности (на случай длинных имён)
    fmt_hash = hashlib.md5(fmt_key.encode()).hexdigest()[:8]
    return os.path.join('encoded', f"{ad_basename}_{fmt_hash}.mkv")

def normalize_ad(main_video_props, ad_file, output_file):
    """Перекодирует рекламный ролик в формат основного фильма."""
    width = main_video_props['width']
    height = main_video_props['height']
    fps = main_video_props['fps']
    pix_fmt = main_video_props.get('pix_fmt', 'yuv420p')
    
    v_encoder = get_ffmpeg_encoder(main_video_props['v_codec'])
    a_encoder = get_ffmpeg_audio_encoder(main_video_props['a_codec'])
    a_sample_rate = main_video_props['a_sample_rate']
    a_channels = main_video_props['a_channels']
    quality_args = get_video_quality_args(v_encoder)
    
    # Проверяем, есть ли звук в самой рекламе
    has_audio = False
    try:
        cmd_a = ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', ad_file]
        res = subprocess.run(cmd_a, stdout=subprocess.PIPE, text=True).stdout.strip()
        if res:
            has_audio = True
    except:
        pass

    cmd = ['ffmpeg', '-y', '-i', ad_file]
    
    # Если звука нет, генерируем тишину, иначе concat вырежет звук из всего фильма!
    if not has_audio:
        cmd.extend(['-f', 'lavfi', '-i', f'anullsrc=r={a_sample_rate}:cl=stereo'])
        
    cmd.extend([
        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
        '-r', str(fps),
        '-c:v', v_encoder,
    ])
    cmd.extend(quality_args)
    cmd.extend([
        '-pix_fmt', pix_fmt,
        '-c:a', a_encoder,
        '-ar', str(a_sample_rate),
        '-ac', str(a_channels)
    ])
    
    if not has_audio:
        # Привязываем длину звука к длине видео
        cmd.extend(['-shortest'])
        # Указываем маппинг
        cmd.extend(['-map', '0:v:0', '-map', '1:a:0'])

    cmd.append(output_file)

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def process_job(job_name, main_video, sequence, job_index):
    if not main_video or not os.path.exists(main_video):
        print(f"[{job_name}] ❌ Ошибка: Главный фильм '{main_video}' не найден.")
        return

    print(f"\n🎬 Начинаю сборку: {job_name}")
    print(f"  🔍 Анализирую параметры фильма '{main_video}'...")
    main_props = get_video_properties(main_video)
    
    if not main_props:
        print(f"[{job_name}] ❌ Ошибка: Не удалось получить параметры фильма через ffprobe.")
        return

    v_encoder = get_ffmpeg_encoder(main_props['v_codec'])
    print(f"  📋 Формат фильма: {main_props['v_codec']}/{main_props['a_codec']}, {main_props['width']}x{main_props['height']}")
    print(f"  📋 Фрагменты фильма: -c copy | Вставки будут перекодированы в: {v_encoder}/{main_props['a_codec']}")

    # Создаём папку для кэша перекодированных роликов
    os.makedirs('encoded', exist_ok=True)

    concat_list = []
    part_index = 0
    temp_files = []  # только temp-файлы фрагментов фильма (будут удалены)
    
    for item in sequence:
        item_clean = re.sub(r'^(clip:\s*|-\s*)', '', str(item), flags=re.IGNORECASE).strip()
        match = re.match(r'^([\d\.:]+)\s*-\s*([\d\.:]+|end)$', item_clean, re.IGNORECASE)
        
        if match:
            start_time = parse_time(match.group(1))
            end_time = match.group(2).lower()
            
            part_name = f"temp_{job_index}_part_{part_index}.mkv"
            
            if end_time != 'end':
                start_sec = time_str_to_seconds(start_time)
                end_sec = time_str_to_seconds(parse_time(end_time))
                duration = end_sec - start_sec
                if duration <= 0:
                    print(f"  ⚠️ Неверный диапазон времени: {start_time} -> {end_time}. Пропускаю.")
                    continue
            else:
                duration = None  # до конца файла

            # Фрагменты фильма всегда вырезаются без перекодировки
            print(f"  ⏳ Вырезаю фрагмент фильма (copy): {start_time} -> {end_time}...")
            cmd = ['ffmpeg', '-y', '-ss', start_time, '-fflags', '+genpts', '-i', main_video]
            if duration is not None:
                cmd.extend(['-t', str(duration)])
            cmd.extend(['-c', 'copy', part_name])
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            concat_list.append(part_name)
            temp_files.append(part_name)
            part_index += 1
        else:
            ad_file = item_clean
            if os.path.exists(ad_file):
                # Проверяем, есть ли уже перекодированная версия в encoded/
                encoded_path = get_encoded_ad_path(ad_file, main_props)
                
                if os.path.exists(encoded_path):
                    # Уже есть готовый файл — переиспользуем
                    print(f"  ♻️  Ролик '{ad_file}' уже перекодирован, беру из кэша: {encoded_path}")
                    concat_list.append(encoded_path)
                else:
                    # Перекодируем и сохраняем в encoded/
                    print(f"  ⚙️  Подгоняю ролик '{ad_file}' под формат фильма ({main_props['v_codec']}/{main_props['a_codec']}, {main_props['a_channels']}ch)...")
                    normalize_ad(main_props, ad_file, encoded_path)
                    print(f"  💾 Сохранён в кэш: {encoded_path}")
                    concat_list.append(encoded_path)
            else:
                print(f"  ⚠️ Внимание: Ролик '{ad_file}' не найден! Пропускаю.")

    if not concat_list:
        print(f"[{job_name}] ❌ Не найдено валидных фрагментов для склейки.")
        return

    concat_file = f"concat_list_{job_index}.txt"
    with open(concat_file, 'w', encoding='utf-8') as f:
        for item in concat_list:
            f.write(f"file '{item}'\n")
            
    print(f"  🔄 Склеиваю {job_name}...")
    concat_cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', 
        '-i', concat_file, '-c', 'copy', job_name
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"🎉 Готово: {job_name} сохранен!")
    
    # Зачистка — удаляем только temp-фрагменты фильма, перекодированные ролики остаются в encoded/
    print(f"  🧹 Удаляю временные файлы для {job_name}...")
    for item in temp_files:
        try:
            os.remove(item)
        except:
            pass
    try:
        os.remove(concat_file)
    except:
        pass

def main():
    parser = argparse.ArgumentParser(description="Скрипт для пакетной нарезки и склейки трейлеров (через YAML).")
    parser.add_argument("-c", "--config", default="config.yaml", help="Путь к файлу конфигурации")
    args = parser.parse_args()

    config_file = args.config

    if not os.path.exists(config_file):
        print(f"Файл '{config_file}' не найден. Пожалуйста, создайте его.")
        return

    with open(config_file, 'r', encoding='utf-8') as f:
        try:
            jobs = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Ошибка при чтении YAML файла: {e}")
            return

    if not isinstance(jobs, list):
        print("Ошибка: YAML файл должен содержать массив (список) задач.")
        return
        
    for job_index, job in enumerate(jobs):
        job_name = job.get("output", f"output_{job_index}.mkv")
        main_video = job.get("movie")
        sequence = job.get("sequence", [])
        process_job(job_name, main_video, sequence, job_index)

if __name__ == "__main__":
    main()
