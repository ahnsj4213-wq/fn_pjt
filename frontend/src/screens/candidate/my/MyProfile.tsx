import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { fetchProfile, saveProfile, sendPasswordLink, type Profile } from '@/api/candidate';
import { SESSION_KEY } from '@/app/session';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';
import { Field } from '@/shared/ui/wf';
import { formatPhone, maxBirthDate } from '@/screens/common/signupForm';

const PHONE = /^010-\d{4}-\d{4}$/;

/** SC-A11 마이페이지 · 프로필: account details (the id cannot change) and the password-change mail. */
export function MyProfile() {
  const qc = useQueryClient();
  const toast = useToast();
  const profile = useQuery({ queryKey: ['profile'], queryFn: fetchProfile });
  const [form, setForm] = useState<Omit<Profile, 'email'>>({ name: '', birth_date: null, gender: null, phone: '' });
  const [errors, setErrors] = useState<{ name?: string; phone?: string }>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (profile.data) {
      const { name, birth_date, gender, phone } = profile.data;
      setForm({ name, birth_date, gender, phone: phone ?? '' });
    }
  }, [profile.data]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const next = {
      name: form.name.trim() ? undefined : '이름을 입력해 주십시오',
      phone: PHONE.test(form.phone ?? '') ? undefined : '010-0000-0000 형식으로 입력해 주십시오',
    };
    setErrors(next);
    if (next.name || next.phone) return;
    setBusy(true);
    try {
      const saved = await saveProfile({ ...form, name: form.name.trim(), birth_date: form.birth_date || null });
      qc.setQueryData(['profile'], saved);
      await qc.invalidateQueries({ queryKey: SESSION_KEY }); // the top bar shows the new name
      toast('프로필을 저장했습니다');
    } catch (err) {
      toast(describeError(err).message);
    } finally {
      setBusy(false);
    }
  };

  const passwordLink = async () => {
    try {
      const r = await sendPasswordLink();
      toast(`${r.email}로 비밀번호 변경 링크를 보냈습니다`);
    } catch (err) {
      toast(describeError(err).message);
    }
  };

  return (
    <>
      <div className="ph"><div><h1>프로필</h1><p>지원한 기업이 결과와 일정을 안내할 때 이 정보로 연락합니다.</p></div></div>
      <QueryState isPending={profile.isPending} error={profile.error} onRetry={() => profile.refetch()}>
        {() => (
          <form className="card form" onSubmit={submit} noValidate style={{ maxWidth: 640 }} aria-label="프로필">
            <div className="row2">
              <Field label="이름" id="pr-name" value={form.name} error={errors.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
              <Field label="아이디 (이메일)" id="pr-id" value={profile.data!.email} readOnly aria-readonly="true"
                hint="아이디는 바꿀 수 없습니다" />
            </div>
            <div className="row2">
              <Field label="생년월일" id="pr-birth" type="date" max={maxBirthDate()} value={form.birth_date ?? ''}
                onChange={(e) => setForm((f) => ({ ...f, birth_date: e.target.value || null }))} />
              <div className="field">
                <span className="flabel" id="pr-g-l">성별</span>
                <div className="radios" role="radiogroup" aria-labelledby="pr-g-l">
                  {([['M', '남'], ['F', '여']] as const).map(([v, l]) => (
                    <label key={v}><input type="radio" name="pr-g" value={v} checked={form.gender === v}
                      onChange={() => setForm((f) => ({ ...f, gender: v }))} /> {l}</label>
                  ))}
                </div>
              </div>
            </div>
            <Field label="연락처" id="pr-ph" inputMode="numeric" value={form.phone ?? ''} error={errors.phone}
              onChange={(e) => setForm((f) => ({ ...f, phone: formatPhone(e.target.value) }))} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" type="submit" disabled={busy}>저장</button>
              <button className="btn" type="button" onClick={passwordLink}>비밀번호 변경</button>
            </div>
          </form>
        )}
      </QueryState>
      {/* USR-07: withdrawal stays reachable (not on the wireframe; kept as a small link under the form). */}
      <p className="fine" style={{ margin: 0 }}><Link to="/me/account">회원 탈퇴</Link></p>
    </>
  );
}
