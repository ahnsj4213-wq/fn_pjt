import { zodResolver } from '@hookform/resolvers/zod';
import { useState, type ReactNode } from 'react';
import { useFieldArray, useForm, type FieldPath } from 'react-hook-form';
import type { ResumeContent } from '@/api/common/resume';
import { describeError } from '@/shared/http/errors';
import { Button, Notice, TextField, uiStyles as s } from '@/shared/ui/components';
import { TextAreaField } from '@/shared/ui/extra';
import { meetsMinimum, RES06_MESSAGE, resumeFormSchema, toContent, toForm, type ResumeForm } from './resumeForm';

/**
 * Standard-field resume editor (RES-03 review, RES-05 edit, RES-06 manual entry, RES-07 registered copy).
 * `onSubmit` may throw an ApiError; 422 `content.*` fields map back onto the inputs.
 */
export function ResumeEditor({ initial, onSubmit, submitLabel, children }: {
  initial: ResumeContent;
  onSubmit: (content: ResumeContent) => Promise<void>;
  submitLabel: string;
  children?: ReactNode;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const { register, control, handleSubmit, setError, formState: { errors, isSubmitting } } = useForm<ResumeForm>({
    resolver: zodResolver(resumeFormSchema),
    defaultValues: toForm(initial),
  });
  const education = useFieldArray({ control, name: 'education' });
  const career = useFieldArray({ control, name: 'career' });
  const projects = useFieldArray({ control, name: 'projects' });
  const certificates = useFieldArray({ control, name: 'certificates' });

  const submit = async (values: ResumeForm) => {
    setFormError(null);
    if (!meetsMinimum(values)) {
      setFormError(RES06_MESSAGE);
      return;
    }
    try {
      await onSubmit(toContent(values));
    } catch (e) {
      const d = describeError(e);
      let mapped = false;
      for (const [field, message] of Object.entries(d.fieldErrors)) {
        const name = field.replace(/^content\./, '');
        if (name === 'skills' || name.startsWith('skills.')) {
          setError('skillsText', { message });
          mapped = true;
        } else if (name !== field || !name.includes('.')) {
          setError(name as FieldPath<ResumeForm>, { message });
          mapped = true;
        }
      }
      setFormError(mapped ? d.formErrors[0] ?? d.message : d.message);
    }
  };

  const err = (path: string): string | undefined => {
    let cur: unknown = errors;
    for (const part of path.split('.')) cur = (cur as Record<string, unknown> | undefined)?.[part];
    return (cur as { message?: string } | undefined)?.message;
  };

  return (
    <form onSubmit={handleSubmit(submit)} noValidate aria-label="이력서">
      {formError && <Notice kind="error">{formError}</Notice>}
      <p className={s.muted}>성명과 학력 또는 경력 1건 이상은 꼭 입력해 주세요.</p>
      <TextField label="성명" {...register('name')} error={errors.name?.message} />

      <fieldset className={s.card}>
        <legend className={s.label}>학력</legend>
        {education.fields.map((f, i) => (
          <div key={f.id} className={s.card}>
            <TextField label={`학교명 ${i + 1}`} {...register(`education.${i}.school`)} error={err(`education.${i}.school`)} />
            <TextField label="전공" {...register(`education.${i}.major`)} error={err(`education.${i}.major`)} />
            <TextField label="학위" {...register(`education.${i}.degree`)} error={err(`education.${i}.degree`)} />
            <TextField label="기간" {...register(`education.${i}.period`)} error={err(`education.${i}.period`)} />
            <Button onClick={() => education.remove(i)}>학력 {i + 1} 삭제</Button>
          </div>
        ))}
        <Button onClick={() => education.append({ school: '', major: '', degree: '', period: '' })}>학력 추가</Button>
      </fieldset>

      <fieldset className={s.card}>
        <legend className={s.label}>경력</legend>
        {career.fields.map((f, i) => (
          <div key={f.id} className={s.card}>
            <TextField label={`회사명 ${i + 1}`} {...register(`career.${i}.company`)} error={err(`career.${i}.company`)} />
            <TextField label="직무" {...register(`career.${i}.role`)} error={err(`career.${i}.role`)} />
            <TextField label="기간" {...register(`career.${i}.period`)} error={err(`career.${i}.period`)} />
            <TextAreaField label="설명" {...register(`career.${i}.description`)} error={err(`career.${i}.description`)} />
            <Button onClick={() => career.remove(i)}>경력 {i + 1} 삭제</Button>
          </div>
        ))}
        <Button onClick={() => career.append({ company: '', role: '', period: '', description: '' })}>경력 추가</Button>
      </fieldset>

      <fieldset className={s.card}>
        <legend className={s.label}>프로젝트</legend>
        {projects.fields.map((f, i) => (
          <div key={f.id} className={s.card}>
            <TextField label={`프로젝트명 ${i + 1}`} {...register(`projects.${i}.title`)} error={err(`projects.${i}.title`)} />
            <TextField label="역할" {...register(`projects.${i}.role`)} error={err(`projects.${i}.role`)} />
            <TextField label="기간" {...register(`projects.${i}.period`)} error={err(`projects.${i}.period`)} />
            <TextAreaField label="설명" {...register(`projects.${i}.description`)} error={err(`projects.${i}.description`)} />
            <Button onClick={() => projects.remove(i)}>프로젝트 {i + 1} 삭제</Button>
          </div>
        ))}
        <Button onClick={() => projects.append({ title: '', role: '', period: '', description: '' })}>프로젝트 추가</Button>
      </fieldset>

      <TextField label="기술 스택" hint="쉼표(,)로 구분해 입력해 주세요." {...register('skillsText')} error={errors.skillsText?.message} />

      <fieldset className={s.card}>
        <legend className={s.label}>자격증</legend>
        {certificates.fields.map((f, i) => (
          <div key={f.id} className={s.card}>
            <TextField label={`자격증명 ${i + 1}`} {...register(`certificates.${i}.name`)} error={err(`certificates.${i}.name`)} />
            <TextField label="발급 기관" {...register(`certificates.${i}.issuer`)} error={err(`certificates.${i}.issuer`)} />
            <TextField label="취득일" {...register(`certificates.${i}.date`)} error={err(`certificates.${i}.date`)} />
            <Button onClick={() => certificates.remove(i)}>자격증 {i + 1} 삭제</Button>
          </div>
        ))}
        <Button onClick={() => certificates.append({ name: '', issuer: '', date: '' })}>자격증 추가</Button>
      </fieldset>

      {children}
      <Button type="submit" variant="primary" busy={isSubmitting}>{submitLabel}</Button>
    </form>
  );
}
