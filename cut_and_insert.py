import os
import subprocess
import re
import argparse
import sys
import json

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

def normalize_ad(main_video_props, ad_file, output_file):
    width = main_video_props['width']
    height = main_video_props['height']
    fps = main_video_props['fps']
    
    a_codec = main_video_props['a_codec']
    # ffmpeg использует 'libmp3lame' для mp3
    if a_codec == 'mp3':
        a_codec = 'libmp3lame'
        
    a_sample_rate = main_video_props['a_sample_rate']
    a_channels = main_video_props['a_channels']
    
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
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
        '-c:a', a_codec,
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

    concat_list = []
    part_index = 0
    ad_index = 0
    
    for item in sequence:
        item_clean = re.sub(r'^(clip:\s*|-\s*)', '', str(item), flags=re.IGNORECASE).strip()
        match = re.match(r'^([\d\.:]+)\s*-\s*([\d\.:]+|end)$', item_clean, re.IGNORECASE)
        
        if match:
            start_time = parse_time(match.group(1))
            end_time = match.group(2).lower()
            
            part_name = f"temp_{job_index}_part_{part_index}.mkv"
            print(f"  ⏳ Вырезаю фрагмент фильма: {start_time} -> {end_time}...")

            # Use -ss before -i for more reliable seeking on containers like AVI.
            # When an end time is provided, compute duration and pass with -t.
            if end_time != 'end':
                start_sec = time_str_to_seconds(start_time)
                end_sec = time_str_to_seconds(parse_time(end_time))
                duration = end_sec - start_sec
                if duration <= 0:
                    print(f"  ⚠️ Неверный диапазон времени: {start_time} -> {end_time}. Пропускаю.")
                    continue
                cmd = ['ffmpeg', '-y', '-ss', start_time, '-fflags', '+genpts', '-i', main_video, '-t', str(duration), '-c', 'copy', part_name]
            else:
                cmd = ['ffmpeg', '-y', '-ss', start_time, '-fflags', '+genpts', '-i', main_video, '-c', 'copy', part_name]

            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            concat_list.append(part_name)
            part_index += 1
        else:
            ad_file = item_clean
            if os.path.exists(ad_file):
                normalized_ad_name = f"temp_{job_index}_ad_{ad_index}.mkv"
                print(f"  ⚙️  Подгоняю ролик '{ad_file}' под формат фильма (Звук: {main_props['a_codec']}, {main_props['a_channels']}ch)...")
                normalize_ad(main_props, ad_file, normalized_ad_name)
                concat_list.append(normalized_ad_name)
                ad_index += 1
            else:
                print(f"  ⚠️ Внимание: Ролик '{ad_file}' не найден! Пропускаю.")

    if not concat_list:
        print(f"[{job_name}] ❌ Не найдено валидных фрагментов для склейки.")
        return

    concat_file = f"concat_list_{job_index}.txt"
    with open(concat_file, 'w', encoding='utf-8') as f:
        for item in concat_list:
            f.write(f"file '{item}'\n")
            
    print(f"  🔄 Склеиваю {job_name} (без перекодировки вырезанных фрагментов)...")
    concat_cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', 
        '-i', concat_file, '-c', 'copy', job_name
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"🎉 Готово: {job_name} сохранен!")
    
    # Зачистка
    print(f"  🧹 Удаляю временные файлы для {job_name}...")
    for item in concat_list:
        if item.startswith(f"temp_{job_index}_"):
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
